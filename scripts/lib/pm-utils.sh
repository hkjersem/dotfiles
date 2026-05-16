#!/usr/bin/env bash
# pm-utils.sh — shared utilities for package-manager and audit scripts.
# Sourced by audit-repo.sh, _update-lib.sh, and bun-update.sh.

# Convert minutes to human-readable duration (e.g. "2d 3h", "45m").
format_duration() {
  local mins="$1"
  local days=$(( mins / 1440 )) hours=$(( (mins % 1440) / 60 )) rem=$(( mins % 60 ))
  local out=""
  [[ $days  -gt 0 ]] && out="${days}d"
  [[ $hours -gt 0 ]] && out="${out:+$out }${hours}h"
  [[ $days -eq 0 && $rem -gt 0 ]] && out="${out:+$out }${rem}m"
  echo "${out:-0m}"
}

# Convert a bun/human duration string or bare seconds integer to minutes.
# Accepts: 259200 (bare seconds integer), "3 days", "2 weeks", "6 hours", "24 minutes", "300 seconds"
# Prints minutes, or nothing if the unit is unrecognized or num is non-integer.
parse_duration_minutes() {
  local dur="${1//\"/}"; dur="${dur//\'/}"
  local num unit
  read -r num unit <<< "$dur"
  [[ "$num" =~ ^[0-9]+$ ]] || { echo ""; return; }
  case "${unit%s}" in  # strip trailing 's'
    second) echo $(( num / 60 )) ;;
    minute) echo "$num" ;;
    hour)   echo $(( num * 60 )) ;;
    day)    echo $(( num * 1440 )) ;;
    week)   echo $(( num * 10080 )) ;;
    "")     echo $(( num / 60 )) ;;  # bare integer = seconds
    *)      echo "" ;;
  esac
}

# Extract the raw minimumReleaseAge value from bunfig.toml [install] section.
# Usage: read_bunfig_mra <file>
# Prints the raw value (e.g. 259200, or "3 days") or nothing if not set.
read_bunfig_mra() {
  awk '/^\[install\]/{f=1;next} /^\[/{f=0} f && /^[[:space:]]*minimumReleaseAge[[:space:]]*=/' \
    "${1:?bunfig file required}" 2>/dev/null \
    | head -1 | sed 's/.*=[[:space:]]*//' | tr -d "\"'" | sed 's/[[:space:]]*#.*//' | xargs
}

# Read release-age from an .npmrc file and populate cooldown globals.
# Usage: read_npmrc_release_age <file> <unit> [key_re]
#   unit:   "days" (converts value × 1440 to minutes) or "minutes" (uses value as-is)
#   key_re: ERE alternation of accepted key names (default: both npm key variants)
# Modifies globals: COOLDOWN_MINUTES, COOLDOWN_SOURCE, CONFIG_RELEASE_AGE
read_npmrc_release_age() {
  local file="$1" unit="${2:-days}" key_re="${3:-minimum-release-age|min-release-age}"
  local raw mins
  raw=$(grep -E "^(${key_re})[[:space:]]*=" "$file" \
        | grep -oE '[0-9]+' | head -1 || true)
  [[ -z "$raw" ]] && return
  if [[ "$unit" == "days" ]]; then
    mins=$(( raw * 1440 ))
  else
    mins="$raw"
  fi
  [[ -z "$CONFIG_RELEASE_AGE" ]] && CONFIG_RELEASE_AGE="$mins"
  if [[ -z "$COOLDOWN_MINUTES" ]]; then
    COOLDOWN_MINUTES="$mins"
    COOLDOWN_SOURCE="$file"
  fi
}

# Append release-age exclude entries from an .npmrc file to COOLDOWN_EXCLUDE.
# Usage: read_npmrc_exclude <file> [key_re]
#   key_re: ERE alternation of accepted exclude key names (default: both npm key variants)
# Modifies global: COOLDOWN_EXCLUDE (array)
read_npmrc_exclude() {
  local file="$1" key_re="${2:-minimum-release-age-exclude|min-release-age-exclude}"
  local _line _val _inline _parts
  [[ -f "$file" ]] || return
  while IFS= read -r _line; do
    _val=$(echo "$_line" | sed -E "s/^(${key_re})\[\][[:space:]]*=[[:space:]]*//")
    [[ -n "$_val" ]] && COOLDOWN_EXCLUDE+=("$_val")
  done < <(grep -E "^(${key_re})\[\]" "$file" 2>/dev/null || true)
  _inline=$(grep -E "^(${key_re})[[:space:]]*=" "$file" 2>/dev/null \
            | head -1 | sed 's/^[^=]*=[[:space:]]*//' || true)
  if [[ -n "$_inline" ]]; then
    read -ra _parts <<< "$_inline"
    COOLDOWN_EXCLUDE+=("${_parts[@]}")
  fi
}
