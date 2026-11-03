#!/bin/bash
# Root helper for agent-awake. launchd runs it when a hold or wake request changes and every 30 seconds.
# While an agent hold or a scheduled wake window is active, it sets pmset disablesleep, the only switch that
# keeps a MacBook awake with its lid closed. When the last one ends it clears it again and, with the lid
# closed and no external display, puts the Mac to sleep. It also schedules the requested wakes with pmset.
#
# It runs as root, so it is self-contained: it only reads the user's hold files, never writes, deletes or
# sources anything in the user's home, and is installed as a root-owned copy outside the repository.

set -u
# Tests put stand-ins for pmset, ioreg and the like first; launchd and sudo never pass this variable on.
PATH=${AGENT_AWAKE_TEST_PATH:+$AGENT_AWAKE_TEST_PATH:}/usr/bin:/bin:/usr/sbin:/sbin

LABEL=com.hkjersem.agent-awake
HELPER=/Library/PrivilegedHelperTools/$LABEL.sh
PLIST=/Library/LaunchDaemons/$LABEL.plist
LOG=/var/log/agent-awake.log
OWNER=agent-awake
# A hold that agent activity has not refreshed for this long is ignored, as after an interrupted turn.
LEASE_SECONDS=1200
# While a tool call runs, such as a long build, no hook fires until it ends, so a busy hold lasts longer.
BUSY_SECONDS=7200
# A released hold still counts this long, so a turn that continues right away, or a subagent that is still
# working, does not let a closed Mac fall asleep in between.
RELEASE_GRACE_SECONDS=45
# A wake request keeps the Mac awake from this long before its time until it is withdrawn or this long after.
WAKE_BEFORE_SECONDS=120
WAKE_AFTER_SECONDS=600
# A scheduled wake is often only a dark wake of a few seconds, which can end before launchd starts this
# helper. So each request gets a wake this many seconds before its time; the first one the helper catches
# keeps the Mac awake, and the rest then change nothing.
WAKE_OFFSETS="90 60 30 0"
# After sleep was disabled, macOS takes a moment to report that closing the lid causes sleep again. In a
# dark wake it does not report it at all, and macOS soon sleeps by itself. The helper checks this many
# times, two seconds apart, before leaving sleep to macOS.
LID_CHECKS=5
WAKE_HORIZON_SECONDS=$((8 * 24 * 3600))

usage() {
    cat <<'EOF'
Usage: agent-awake-daemon.sh --install|--reconcile --home DIR --battery PERCENT
       agent-awake-daemon.sh --uninstall

Root helper for agent-awake; run it through agent-awake, not directly.

  --install       copy this script to /Library/PrivilegedHelperTools and load its LaunchDaemon
  --uninstall     unload and remove the LaunchDaemon, allow sleep again and cancel its wakes
  --reconcile     apply the current holds and wake requests once (run by launchd)
  --home DIR      home folder whose ~/.cache/agent-awake holds and wake requests are read
  --battery PCT   on battery power, let the Mac sleep below this charge
  -h, --help      show this help
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

log() {
    printf '[%s] %s\n' "$(date '+%d.%m.%Y kl. %H.%M.%S')" "$*"
}

# The epoch time in a wake request named <source>.<epoch>.
wake_time() {
    local stamp=${1##*.}
    case "$stamp" in
        ''|0*|*[!0-9]*) return 1 ;;
    esac
    [ ${#stamp} -ge 9 ] && [ ${#stamp} -le 11 ] || return 1
    printf '%s\n' "$stamp"
}

# Prints the first active hold or wake window; fails when there is none.
active_hold() {
    local now=$1 file content pid mtime limit stamp
    for file in "$HOLDS"/*; do
        [ -f "$file" ] && [ ! -L "$file" ] || continue
        mtime=$(stat -f %m "$file" 2>/dev/null) || continue
        content=$(head -c 64 "$file" 2>/dev/null)
        limit=$LEASE_SECONDS
        case "$content" in
            *released*) limit=$RELEASE_GRACE_SECONDS ;;
            *busy*) limit=$BUSY_SECONDS ;;
        esac
        [ $((now - mtime)) -lt "$limit" ] || continue
        pid=$(printf '%s\n' "$content" | head -n 1 | tr -cd '0-9')
        if [ -n "$pid" ] && [ ${#pid} -le 7 ] && [ "$pid" -gt 1 ]; then
            kill -0 "$pid" 2>/dev/null || continue
        fi
        printf '%s\n' "${file##*/}" | tr -cd 'A-Za-z0-9._-'
        return 0
    done
    for file in "$WAKES"/*; do
        [ -f "$file" ] && [ ! -L "$file" ] || continue
        stamp=$(wake_time "$file") || continue
        if [ "$now" -ge $((stamp - WAKE_BEFORE_SECONDS)) ] && [ "$now" -lt $((stamp + WAKE_AFTER_SECONDS)) ]; then
            printf 'wake request %s\n' "${file##*/}" | tr -cd 'A-Za-z0-9._ -'
            return 0
        fi
    done
    return 1
}

# Wakes this helper scheduled, as pmset lists them: MM/DD/YYYY HH:MM:SS.
scheduled_wakes() {
    pmset -g sched 2>/dev/null | sed -n "s|.*wake at \([0-9/]* [0-9:]*\) by '$OWNER'.*|\1|p"
}

cancel_wake() {
    local listed=$1 stamp
    stamp=$(date -j -f '%m/%d/%Y %H:%M:%S' "$listed" +%s 2>/dev/null) || return 0
    pmset schedule cancel wake "$(date -r "$stamp" '+%m/%d/%y %H:%M:%S')" "$OWNER" >/dev/null 2>&1 &&
        log "Cancelled the wake at $listed."
}

# Schedules the wakes for each future request and cancels this helper's wakes that no request asks for.
sync_wakes() {
    local now=$1 file stamp offset at listed existing wanted=""
    existing=$(scheduled_wakes)
    for file in "$WAKES"/*; do
        [ -f "$file" ] && [ ! -L "$file" ] || continue
        stamp=$(wake_time "$file") || continue
        for offset in $WAKE_OFFSETS; do
            at=$((stamp - offset))
            [ "$at" -gt "$now" ] && [ "$at" -lt $((now + WAKE_HORIZON_SECONDS)) ] || continue
            listed=$(date -r "$at" '+%m/%d/%Y %H:%M:%S')
            wanted="$wanted$listed
"
            # A wake this close is already scheduled or no longer needed: the Mac is awake now.
            [ "$at" -gt $((now + 30)) ] || continue
            printf '%s\n' "$existing" | grep -Fxq "$listed" && continue
            if pmset schedule wake "$(date -r "$at" '+%m/%d/%y %H:%M:%S')" "$OWNER" >/dev/null 2>&1; then
                log "Scheduled a wake at $listed."
            else
                log "Could not schedule a wake at $listed."
            fi
        done
    done
    printf '%s\n' "$existing" | while IFS= read -r listed; do
        [ -n "$listed" ] || continue
        printf '%s' "$wanted" | grep -Fxq "$listed" && continue
        cancel_wake "$listed"
    done
}

battery_low() {
    local info percent
    info=$(pmset -g batt 2>/dev/null) || return 1
    case "$info" in
        *"'Battery Power'"*) ;;
        *) return 1 ;;
    esac
    percent=$(printf '%s\n' "$info" | sed -n 's/.*[[:space:]]\([0-9][0-9]*\)%;.*/\1/p' | head -n 1)
    [ -n "$percent" ] && [ "$percent" -lt "$BATTERY" ]
}

sleep_disabled() {
    pmset -g 2>/dev/null | awk '$1 == "SleepDisabled" { print $2 }'
}

# Prints open, sleeps (closed, and closing it puts the Mac to sleep) or awake (closed, but something such as
# an external display or disabled sleep keeps the Mac awake).
lid_state() {
    local state
    state=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null)
    case "$state" in
        *'"AppleClamshellState" = Yes'*) ;;
        *) echo open; return 0 ;;
    esac
    case "$state" in
        *'"AppleClamshellCausesSleep" = Yes'*) echo sleeps ;;
        *) echo awake ;;
    esac
}

# A prompt submitted while the helper waits keeps the Mac awake after all.
held_again() {
    local reason
    reason=$(active_hold "$(date +%s)") && ! battery_low || return 1
    pmset -a disablesleep 1 && log "Keeping the Mac awake for $reason."
    return 0
}

reconcile() {
    local now reason want=0
    now=$(date +%s)
    sync_wakes "$now"
    reason=$(active_hold "$now") || reason=""
    if [ -n "$reason" ] && ! battery_low; then
        want=1
    fi
    if [ "$want" = 1 ]; then
        [ "$(sleep_disabled)" = 1 ] && return 0
        pmset -a disablesleep 1 && log "Keeping the Mac awake for $reason."
        return 0
    fi
    [ "$(sleep_disabled)" = 1 ] || return 0
    pmset -a disablesleep 0 || return 1
    if [ -n "$reason" ]; then
        log "Battery below $BATTERY%; allowing sleep although $reason is active."
    else
        log "No agent is working; sleep allowed again."
    fi
    local checks=0
    while :; do
        sleep 2
        held_again && return 0
        case "$(lid_state)" in
            open) return 0 ;;
            sleeps) break ;;
        esac
        checks=$((checks + 1))
        if [ "$checks" -ge "$LID_CHECKS" ]; then
            log "The lid is closed, but macOS does not report that it causes sleep, as with an external display or in a dark wake; leaving sleep to macOS."
            return 0
        fi
    done
    log "The lid is closed; sleeping now."
    pmset sleepnow >/dev/null
}

# The holds and wake requests with inode and modification time; hooks replace hold files, so a refresh shows.
snapshot() {
    ls -liT "$HOLDS" "$WAKES" 2>/dev/null
}

# launchd does not start the helper again for a change made while it runs, such as a wake request written
# just after the scan, so it reconciles again until nothing changed during a run.
reconcile_until_settled() {
    local before after status runs=0
    while :; do
        before=$(snapshot)
        reconcile
        status=$?
        after=$(snapshot)
        runs=$((runs + 1))
        [ "$before" != "$after" ] && [ "$runs" -lt 5 ] || return "$status"
    done
}

xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

install_helper() {
    local source home temporary
    source=${BASH_SOURCE[0]}
    home=$(xml_escape "$HOME_DIR")
    install -d -o root -g wheel -m 755 /Library/PrivilegedHelperTools || die "could not create the helper folder."
    install -o root -g wheel -m 755 "$source" "$HELPER" || die "could not install $HELPER."
    temporary=$(mktemp /tmp/agent-awake.XXXXXX) || die "could not create a temporary file."
    cat > "$temporary" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HELPER</string>
    <string>--reconcile</string>
    <string>--home</string>
    <string>$home</string>
    <string>--battery</string>
    <string>$BATTERY</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>30</integer>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>WatchPaths</key>
  <array>
    <string>$home/.cache/agent-awake/holds</string>
    <string>$home/.cache/agent-awake/wake</string>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    if ! plutil -lint -s "$temporary"; then
        rm -f "$temporary"
        die "the LaunchDaemon property list is invalid."
    fi
    launchctl bootout "system/$LABEL" >/dev/null 2>&1
    install -o root -g wheel -m 644 "$temporary" "$PLIST" || { rm -f "$temporary"; die "could not install $PLIST."; }
    rm -f "$temporary"
    launchctl bootstrap system "$PLIST" || die "could not load $PLIST."
}

uninstall_helper() {
    local listed
    launchctl bootout "system/$LABEL" >/dev/null 2>&1
    rm -f "$PLIST" "$HELPER"
    [ "$(sleep_disabled)" = 1 ] && pmset -a disablesleep 0
    scheduled_wakes | while IFS= read -r listed; do
        [ -n "$listed" ] && cancel_wake "$listed" >/dev/null
    done
    return 0
}

ACTION=""
HOME_DIR=""
BATTERY=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --install|--uninstall|--reconcile)
            [ -z "$ACTION" ] || die "use one of --install, --uninstall or --reconcile."
            ACTION=${1#--}
            ;;
        --home) [ $# -ge 2 ] || die "--home needs a folder."; HOME_DIR=$2; shift ;;
        --battery) [ $# -ge 2 ] || die "--battery needs a percentage."; BATTERY=$2; shift ;;
        *) usage >&2; exit 1 ;;
    esac
    shift
done

[ -n "$ACTION" ] || { usage >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "this helper must run as root."
if [ "$ACTION" != uninstall ]; then
    case "$HOME_DIR" in
        /*) ;;
        *) die "--home must be an absolute folder." ;;
    esac
    case "$HOME_DIR" in
        *"
"*) die "--home must be a single line." ;;
    esac
    [ -d "$HOME_DIR" ] || die "--home $HOME_DIR is not a folder."
    case "$BATTERY" in
        ''|*[!0-9]*) die "--battery must be a whole percentage." ;;
    esac
    [ "$BATTERY" -ge 1 ] && [ "$BATTERY" -le 99 ] || die "--battery must be between 1 and 99."
    HOLDS=$HOME_DIR/.cache/agent-awake/holds
    WAKES=$HOME_DIR/.cache/agent-awake/wake
fi

case "$ACTION" in
    install) install_helper ;;
    uninstall) uninstall_helper ;;
    reconcile) reconcile_until_settled ;;
esac
