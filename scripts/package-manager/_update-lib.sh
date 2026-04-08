#!/usr/bin/env bash
# _update-lib.sh — shared logic for pnpm-update.sh and npm-update.sh
# Sourced by wrappers. Not executed directly.
#
# Wrappers must define before calling run_plan:
#   ROOT                — project root (absolute path, cd'd into)
#   OUTDATED            — JSON {pkg: {current, latest}} (normalised)
#   COOLDOWN_MINUTES    — integer minutes or "" (wrappers convert their config to minutes)
#   COOLDOWN_SOURCE     — human-readable label for the cooldown origin
#   COOLDOWN_EXCLUDE    — array of package patterns to skip cooldown for
#   CONFIG_RELEASE_AGE  — raw config value in minutes (pnpm augmentation; npm sets "")
#   DRY_RUN AUTO_YES FORCE_CHECK ALLOW_MAJOR FILTER_PKG CLI_COOLDOWN
#   (set by _parse_args "$@")
#
# Wrappers must define these hooks:
#   _find_location_extra "$pkg"  — return "catalog" or "" (default: "")
#   _apply_catalog "$pkg" "$ver" — write catalog entry (default: warn + skip)
#   _pm_install                  — run the package manager install command

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi

# ── Arg parsing ───────────────────────────────────────────────────────────────
# Call: _parse_args "$@" — sets globals used throughout the pipeline.
_parse_args() {
  DRY_RUN=false
  AUTO_YES=false
  FORCE_CHECK=false
  ALLOW_MAJOR=false
  CLI_COOLDOWN=""
  FILTER_PKG=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run|-n)  DRY_RUN=true; shift ;;
      --yes|-y)      AUTO_YES=true; shift ;;
      --force|-f)    FORCE_CHECK=true; shift ;;
      --major|-m)    ALLOW_MAJOR=true; shift ;;
      --cooldown)
        if [[ $# -lt 2 ]]; then
          echo -e "${RED}Error: --cooldown requires a value${RESET}" >&2; exit 1
        fi
        CLI_COOLDOWN="$2"; shift 2 ;;
      --cooldown=*)  CLI_COOLDOWN="${1#*=}"; shift ;;
      --help|-h)     _show_help; exit 0 ;;
      -*) shift ;;
      *)  FILTER_PKG="$1"; shift ;;
    esac
  done

  # Resolve CLI cooldown → minutes
  if [[ -n "$CLI_COOLDOWN" ]]; then
    if ! [[ "$CLI_COOLDOWN" =~ ^[0-9]+$ ]]; then
      echo -e "${RED}Error: --cooldown value must be a non-negative integer (days)${RESET}" >&2; exit 1
    fi
    if [[ "$CLI_COOLDOWN" -eq 0 ]]; then
      COOLDOWN_MINUTES="0"
    else
      COOLDOWN_MINUTES=$(( CLI_COOLDOWN * 1440 ))
    fi
    COOLDOWN_SOURCE="--cooldown flag"
  fi
}

# Default help — wrappers override with package-manager-specific text
_show_help() {
  echo "Usage: $(basename "$0") [--dry-run] [--yes] [--force] [--major] [--cooldown <days>] [<package>]"
  echo "  <package>         Only update this package"
  echo "  --dry-run         Show planned updates without making changes"
  echo "  --yes             Skip confirmation prompt"
  echo "  --force           Check all exact pins directly via npm"
  echo "  --major           Allow major version bumps"
  echo "  --cooldown <days> Override cooldown in days (0 = disable)"
}

# ── Root finder ───────────────────────────────────────────────────────────────
# Walk up from $PWD; check packageManager field first, then lockfile.
# Sets $ROOT and cd's into it.
_find_root() {
  local lockfile="$1"
  ROOT="$PWD"
  local _d="$PWD"
  while [[ "$_d" != "/" ]]; do
    if [[ -f "$_d/package.json" ]]; then
      local _pm_field
      _pm_field=$(jq -r '.packageManager // empty' "$_d/package.json" 2>/dev/null | cut -d@ -f1 || true)
      if [[ -n "$_pm_field" ]]; then ROOT="$_d"; break; fi
    fi
    if [[ -f "$_d/$lockfile" ]]; then ROOT="$_d"; break; fi
    _d="$(dirname "$_d")"
  done
  cd "$ROOT"
  [[ -f "package.json" ]] || { echo -e "${RED}No package.json found in $ROOT${RESET}" >&2; exit 1; }
}

# ── YAML helper ───────────────────────────────────────────────────────────────
_yaml_strip() { echo "$1" | sed "s/^[[:space:]]*-[[:space:]]*//;s/^['\"]//;s/['\"]$//;s/[[:space:]]*$//"; }

# ── Semver helpers ────────────────────────────────────────────────────────────
# Returns the "breaking" version prefix under caret-range semver rules:
#   4.1.2  → "4"      major > 0
#   0.4.26 → "0.4"    major = 0, minor is breaking
#   0.0.5  → "0.0.5"  major = minor = 0
breaking_prefix() {
  local major minor patch
  major=$(echo "$1" | cut -d. -f1)
  minor=$(echo "$1" | cut -d. -f2)
  patch=$(echo "$1" | cut -d. -f3 | grep -oE '^[0-9]+' || echo "0")
  if   [[ "$major" -gt 0 ]]; then echo "$major"
  elif [[ "$minor" -gt 0 ]]; then echo "0.$minor"
  else                            echo "0.0.${patch}"
  fi
}

# Returns true (exit 0) if $1 is strictly newer than $2 per semver
semver_newer() {
  node -e 'const parse=v=>{const m=v.match(/^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);return m?[+m[1],+m[2],+m[3],m[4]||null]:null};const cmp=(a,b)=>{for(let i=0;i<3;i++)if(a[i]!==b[i])return a[i]-b[i];if(!a[3]&&!b[3])return 0;if(!a[3])return 1;if(!b[3])return -1;return a[3]<b[3]?-1:1};const[a,b]=[process.argv[1],process.argv[2]].map(parse);process.exit(a&&b&&cmp(a,b)>0?0:1)' "$1" "$2" 2>/dev/null
}

latest_in_prefix() {
  local pkg="$1" prefix="$2" result
  result=$(npm view "${pkg}@^${prefix}" version --json 2>/dev/null) || true
  [[ -z "$result" || "$result" == "null" ]] && echo "" && return
  echo "$result" | jq -r 'if type == "array" then last else . end' 2>/dev/null || echo ""
}

latest_prerelease_in_major() {
  local pkg="$1" major="$2"
  npm view "$pkg" versions --json 2>/dev/null | \
    jq -r ".[] | select(startswith(\"${major}.\") and contains(\"-\"))" 2>/dev/null | \
    sort -V | tail -1
}

# ── Cooldown helpers ──────────────────────────────────────────────────────────
format_duration() {
  local mins="$1"
  local days=$(( mins / 1440 )) hours=$(( (mins % 1440) / 60 )) rem=$(( mins % 60 ))
  local out=""
  [[ $days  -gt 0 ]] && out="${days}d"
  [[ $hours -gt 0 ]] && out="${out:+$out }${hours}h"
  [[ $days -eq 0 && $rem -gt 0 ]] && out="${out:+$out }${rem}m"
  echo "${out:-0m}"
}

version_age_minutes() {
  local pkg="$1" ver="$2" times="${3:-}" published epoch now
  [[ -z "$times" ]] && { times=$(npm view "$pkg" time --json 2>/dev/null) || true; }
  [[ -z "$times" ]] && echo "" && return
  published=$(echo "$times" | jq -r ".\"${ver}\" // empty" 2>/dev/null) || true
  [[ -z "$published" ]] && echo "" && return
  now=$(date +%s)
  epoch=$(node -e "process.stdout.write(String(Math.floor(new Date('${published}').getTime()/1000)))" 2>/dev/null) || true
  [[ -z "$epoch" ]] && echo "" && return
  echo $(( (now - epoch) / 60 ))
}

is_cooldown_excluded() {
  local pkg="$1" entry name_part
  for entry in "${COOLDOWN_EXCLUDE[@]}"; do
    if [[ "$entry" == @* ]]; then
      [[ "$entry" =~ ^(@[^/]+/[^@]+) ]] && name_part="${BASH_REMATCH[1]}" || name_part="$entry"
    else
      name_part="${entry%%@*}"
    fi
    [[ "$pkg" == $name_part ]] && return 0
  done
  return 1
}

# ── Workspace helpers ─────────────────────────────────────────────────────────
# Outputs all glob patterns for workspace members from pnpm-workspace.yaml or
# the root package.json "workspaces" field (npm workspaces).
_workspace_patterns() {
  if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
    awk '/^packages:/{f=1;next} /^[a-zA-Z]/{f=0} f && /^[[:space:]]+-/' \
        "$ROOT/pnpm-workspace.yaml" \
      | sed "s/^[[:space:]]*-[[:space:]]*//;s/['\"]//g;s/[[:space:]]*$//"
  else
    jq -r '
      if   .workspaces | type == "array"  then .workspaces[]
      elif .workspaces | type == "object" then (.workspaces.packages // [])[]
      else empty
      end
    ' "$ROOT/package.json" 2>/dev/null
  fi
}

# Outputs absolute paths to all package.json files: root first, then workspace
# members resolved via _workspace_patterns.
_collect_workspace_pkgjsons() {
  echo "$ROOT/package.json"
  local pattern _wd
  while IFS= read -r pattern; do
    [[ -z "$pattern" ]] && continue
    for _wd in $ROOT/$pattern; do
      [[ -f "$_wd/package.json" ]] && echo "$_wd/package.json"
    done
  done < <(_workspace_patterns)
}

# ── Location detection ────────────────────────────────────────────────────────
# Returns "catalog", "<path/to/package.json>:<depType>", or "unknown".
# Calls _find_location_extra first (package-manager-specific hook, e.g. catalog in pnpm).
find_location() {
  local pkg="$1"

  local extra; extra=$(_find_location_extra "$pkg")
  [[ -n "$extra" ]] && echo "$extra" && return

  local pkgjsons=()
  while IFS= read -r _pj; do pkgjsons+=("$_pj"); done < <(_collect_workspace_pkgjsons)

  local fallback="" pkgjson deptype val
  for pkgjson in "${pkgjsons[@]}"; do
    for deptype in dependencies devDependencies peerDependencies optionalDependencies; do
      val=$(jq -r ".${deptype}[\"${pkg}\"] // empty" "$pkgjson" 2>/dev/null) || continue
      [[ -z "$val" ]] && continue
      [[ "$val" == catalog* ]] && echo "catalog" && return
      if [[ "$val" =~ ^">" || "$val" == "*" ]]; then
        [[ -z "$fallback" ]] && fallback="$pkgjson:$deptype"
        continue
      fi
      echo "$pkgjson:$deptype"; return
    done
  done
  [[ -n "$fallback" ]] && echo "$fallback" || echo "unknown"
}

# Returns all locations (one per line) where a package is pinned to a concrete version.
# Used by write-back to update every workspace member that pins the package.
find_all_locations() {
  local pkg="$1"

  local extra; extra=$(_find_location_extra "$pkg")
  [[ -n "$extra" ]] && echo "$extra" && return

  local pkgjsons=()
  while IFS= read -r _pj; do pkgjsons+=("$_pj"); done < <(_collect_workspace_pkgjsons)

  local found=0 fallback="" pkgjson deptype val
  for pkgjson in "${pkgjsons[@]}"; do
    for deptype in dependencies devDependencies peerDependencies optionalDependencies; do
      val=$(jq -r ".${deptype}[\"${pkg}\"] // empty" "$pkgjson" 2>/dev/null) || continue
      [[ -z "$val" ]] && continue
      [[ "$val" == catalog* ]] && echo "catalog" && return
      if [[ "$val" =~ ^">" || "$val" == "*" ]]; then
        [[ -z "$fallback" ]] && fallback="$pkgjson:$deptype"
        continue
      fi
      echo "$pkgjson:$deptype"; found=1
    done
  done
  [[ $found -eq 0 ]] && { [[ -n "$fallback" ]] && echo "$fallback" || echo "unknown"; }
}

# Default hooks — wrappers override as needed
_find_location_extra() { echo ""; }
_apply_catalog()       { echo -e "  ${YELLOW}⚠ Catalog update not supported${RESET}"; }
_pm_install()          { echo -e "${YELLOW}⚠ _pm_install not defined${RESET}" >&2; }

# ── Augmentation ──────────────────────────────────────────────────────────────
# Re-checks exact-pinned packages that the package manager may suppress via release-age
# Supplements OUTDATED with exact-pinned packages that npm's --json output omits
# (e.g. npm outdated --json silently skips packages where wanted === current), and
# handles cooldown filtering. Injects or updates entries in OUTDATED.
#
# Requires globals: OUTDATED, ROOT, FORCE_CHECK, COOLDOWN_MINUTES, CONFIG_RELEASE_AGE,
#   AUGMENT_ALWAYS (set to true by npm-update.sh to catch packages npm --json omits)
# CONFIG_RELEASE_AGE: the configured release-age from files (minutes); wrappers
#   must convert their native unit to minutes before setting this.
#   Set to "" to disable cooldown augmentation (e.g. if no config was found and
#   --force was not passed).
_augment_outdated() {
  # SHOULD_AUGMENT: re-check packages potentially suppressed by cooldown config.
  # AUGMENT_ALWAYS: always collect exact pins (npm needs this; pnpm does not).
  local SHOULD_AUGMENT=false
  if $FORCE_CHECK; then
    SHOULD_AUGMENT=true
  elif [[ -n "$CONFIG_RELEASE_AGE" && "$CONFIG_RELEASE_AGE" -gt 0 ]]; then
    local effective=${COOLDOWN_MINUTES:-$CONFIG_RELEASE_AGE}
    [[ "$effective" -lt "$CONFIG_RELEASE_AGE" ]] && SHOULD_AUGMENT=true
  fi
  { $SHOULD_AUGMENT || ${AUGMENT_ALWAYS:-false}; } || return 0

  declare -a _aug_pkg=() _aug_pin=()
  declare -A _aug_seen=() _aug_in_outdated=()

  _aug_add() {
    local pkg="$1" pin="$2"
    [[ -z "$pkg" || -z "$pin" ]] && return
    pin="${pin#v}"
    pin="${pin#\^}"; pin="${pin#\~}"; pin="${pin#>=}"; pin="${pin#>}"
    [[ ! "$pin" =~ ^[0-9] ]] && return
    [[ -n "${_aug_seen[$pkg]:-}" ]] && return
    echo "$OUTDATED" | jq -e ".\"${pkg}\"" &>/dev/null && _aug_in_outdated["$pkg"]=1
    _aug_pkg+=("$pkg"); _aug_pin+=("$pin")
    _aug_seen["$pkg"]=1
  }

  # pnpm catalog entries
  if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
    while IFS= read -r line; do
      local pkg pin
      pkg=$(echo "$line" | sed "s/^[[:space:]]*//;s/['\"]//g;s/[[:space:]]*:[[:space:]]*.*//" )
      pin=$(echo "$line" | sed "s/.*:[[:space:]]*//" | tr -d "'\"\r" | xargs)
      _aug_add "$pkg" "$pin"
    done < <(awk '/^catalog:/{f=1;next} /^[a-zA-Z]/{f=0} f' "$ROOT/pnpm-workspace.yaml")
  fi

  # Direct exact pins from package.json files (root + all workspace members)
  declare -a _pkgjsons=()
  while IFS= read -r _pj; do _pkgjsons+=("$_pj"); done < <(_collect_workspace_pkgjsons)
  local _pj pkg pin
  for _pj in "${_pkgjsons[@]}"; do
    while IFS=$'\t' read -r pkg pin; do
      [[ "$pin" == catalog* || "$pin" == workspace* ]] && continue
      _aug_add "$pkg" "$pin"
    done < <(jq -r '
      ["dependencies","devDependencies","peerDependencies","optionalDependencies"] as $t |
      $t[] as $dt | (.[$dt] // {}) | to_entries[] | "\(.key)\t\(.value)"
    ' "$_pj" 2>/dev/null)
  done

  [[ ${#_aug_pkg[@]} -eq 0 ]] && return 0

  echo -ne "  ${DIM}Checking ${#_aug_pkg[@]} exact-pinned packages...${RESET}\r" >&2
  local _aug_tmp; _aug_tmp=$(mktemp -d)
  local i
  for i in "${!_aug_pkg[@]}"; do
    npm view "${_aug_pkg[$i]}" version 2>/dev/null > "$_aug_tmp/$i" &
  done
  wait
  echo -ne "\033[2K" >&2
  for i in "${!_aug_pkg[@]}"; do
    local pkg="${_aug_pkg[$i]}" pin="${_aug_pin[$i]}" latest pm_target
    latest=$(tr -d '"' < "$_aug_tmp/$i" 2>/dev/null | xargs) || continue
    [[ -z "$latest" ]] && continue
    semver_newer "$latest" "$pin" || continue
    if [[ -n "${_aug_in_outdated[$pkg]:-}" ]]; then
      pm_target=$(echo "$OUTDATED" | jq -r ".\"${pkg}\".latest" | tr -d '"'"'")
      [[ "$latest" == "$pm_target" ]] && continue
      semver_newer "$latest" "$pm_target" || continue
      OUTDATED=$(echo "$OUTDATED" | jq \
        --arg pkg "$pkg" --arg latest "$latest" \
        '.[$pkg].latest = $latest | .[$pkg].wanted = $latest')
    else
      [[ "$latest" == "$pin" ]] && continue
      OUTDATED=$(echo "$OUTDATED" | jq \
        --arg pkg "$pkg" --arg current "$pin" --arg latest "$latest" \
        '. + {($pkg): {"current": $current, "latest": $latest, "wanted": $latest}}')
    fi
  done
  rm -rf "$_aug_tmp"
}

# ── Main pipeline ─────────────────────────────────────────────────────────────
# Call after OUTDATED, ROOT, cooldown vars, and hooks are set.
run_plan() {
  # ── Header ─────────────────────────────────────────────────────────────────
  echo -e "${BOLD}Checking for outdated packages...${RESET}  ${DIM}(${PM_NAME:-pnpm})${RESET}"

  if [[ -n "$COOLDOWN_MINUTES" ]]; then
    if [[ "$COOLDOWN_MINUTES" -gt 0 ]]; then
      echo -e "  Cooldown:  $(format_duration "$COOLDOWN_MINUTES") (${COOLDOWN_SOURCE})"
      if [[ ${#COOLDOWN_EXCLUDE[@]} -gt 0 ]]; then
        echo -e "  Excluded:  ${DIM}${COOLDOWN_EXCLUDE[*]}${RESET}"
      fi
    else
      echo -e "  Cooldown:  ${DIM}disabled (--cooldown 0)${RESET}"
    fi
  fi

  # ── Early exit if nothing outdated ─────────────────────────────────────────
  if [[ "$OUTDATED" == "{}" ]]; then
    echo -e "${GREEN}✓ All packages are up to date!${RESET}"; exit 0
  fi

  # ── Filter to single package ────────────────────────────────────────────────
  if [[ -n "$FILTER_PKG" ]]; then
    OUTDATED=$(echo "$OUTDATED" | jq --arg pkg "$FILTER_PKG" '{($pkg): .[$pkg]} | with_entries(select(.value != null))')
    if [[ "$OUTDATED" == "{}" ]]; then
      echo -e "${GREEN}✓ ${FILTER_PKG} is up to date!${RESET}"; exit 0
    fi
  fi

  # ── Build update plan ───────────────────────────────────────────────────────
  declare -a P_PKG=() P_FROM=() P_TO=() P_NOTE=() P_NOTE2=() P_LOC=() P_MAJOR=()
  declare -a S_PKG=() S_FROM=() S_TO=() S_LOC=()
  declare -a R_PKG=() R_FROM=() R_TO=() R_AGE=()

  local COOLDOWN_ACTIVE=false
  [[ -n "$COOLDOWN_MINUTES" && "$COOLDOWN_MINUTES" -gt 0 ]] && COOLDOWN_ACTIVE=true

  # Pre-fetch npm time data (parallel)
  declare -A _PKG_TIMES=()
  if $COOLDOWN_ACTIVE; then
    declare -a _time_pkgs=()
    while IFS= read -r pkg; do
      is_cooldown_excluded "$pkg" && continue
      _time_pkgs+=("$pkg")
    done < <(echo "$OUTDATED" | jq -r 'keys[]')
    if [[ ${#_time_pkgs[@]} -gt 0 ]]; then
      echo -ne "  ${DIM}Fetching version history for ${#_time_pkgs[@]} packages...${RESET}\r" >&2
      local _times_tmp; _times_tmp=$(mktemp -d)
      for i in "${!_time_pkgs[@]}"; do
        npm view "${_time_pkgs[$i]}" time --json 2>/dev/null > "$_times_tmp/$i" &
      done
      wait
      echo -ne "\033[2K" >&2
      for i in "${!_time_pkgs[@]}"; do
        _PKG_TIMES["${_time_pkgs[$i]}"]=$(cat "$_times_tmp/$i" 2>/dev/null || echo "")
      done
      rm -rf "$_times_tmp"
    fi
  fi

  while IFS= read -r pkg; do
    local current; current=$(echo "$OUTDATED" | jq -r ".\"${pkg}\".current" | tr -d '"'"'")
    local latest;  latest=$(echo "$OUTDATED"  | jq -r ".\"${pkg}\".latest"  | tr -d '"'"'")
    [[ "$current" == "null" || "$latest" == "null" ]] && continue

    local current_prefix; current_prefix=$(breaking_prefix "$current")
    local latest_prefix;  latest_prefix=$(breaking_prefix "$latest")
    local loc;            loc=$(find_location "$pkg")

    if [[ "$loc" != "catalog" && "$loc" != "unknown" ]]; then
      local declared; declared=$(jq -r ".${loc##*:}[\"${pkg}\"] // empty" "${loc%%:*}" 2>/dev/null || true)
      [[ "$declared" =~ ^">" || "$declared" == "*" ]] && continue
    fi

    local tgt="" note="" note2="" is_major=false
    if [[ "$current" == *-* ]]; then
      local current_major; current_major=$(echo "$current" | cut -d. -f1)
      local safe_stable;   safe_stable=$(latest_in_prefix "$pkg" "$current_major")
      if [[ -n "$safe_stable" && "$safe_stable" != "$current" ]] && semver_newer "$safe_stable" "$current"; then
        tgt="$safe_stable"; note="was prerelease"
      else
        local safe; safe=$(latest_prerelease_in_major "$pkg" "$current_major")
        if [[ -n "$safe" && "$safe" != "$current" ]] && semver_newer "$safe" "$current"; then
          tgt="$safe"; note="prerelease"
        else
          continue
        fi
      fi
    elif [[ "$latest_prefix" != "$current_prefix" ]]; then
      if $ALLOW_MAJOR; then
        tgt="$latest"; is_major=true
      else
        local safe; safe=$(latest_in_prefix "$pkg" "$current_prefix")
        if [[ -n "$safe" && "$safe" != "$current" ]]; then
          if [[ "$(printf '%s\n%s' "$current" "$safe" | sort -V | tail -1)" != "$safe" ]]; then
            S_PKG+=("$pkg"); S_FROM+=("$current"); S_TO+=("$latest"); S_LOC+=("$loc")
            continue
          fi
          tgt="$safe"; note="${latest} latest"
        else
          S_PKG+=("$pkg"); S_FROM+=("$current"); S_TO+=("$latest"); S_LOC+=("$loc")
          continue
        fi
      fi
    else
      [[ "$latest" == "$current" ]] && continue
      tgt="$latest"
    fi

    if $COOLDOWN_ACTIVE && ! is_cooldown_excluded "$pkg"; then
      local pkg_times; pkg_times="${_PKG_TIMES[$pkg]:-}"
      local age; age=$(version_age_minutes "$pkg" "$tgt" "$pkg_times") || age=""
      if [[ -n "$age" && "$age" -lt "$COOLDOWN_MINUTES" ]]; then
        local fallback_tgt=""
        while IFS= read -r candidate; do
          [[ "$candidate" == "$current" ]] && break
          local cand_age; cand_age=$(version_age_minutes "$pkg" "$candidate" "$pkg_times") || continue
          if [[ -n "$cand_age" && "$cand_age" -ge "$COOLDOWN_MINUTES" ]]; then
            fallback_tgt="$candidate"; break
          fi
        done < <(echo "$pkg_times" | jq -r --arg pfx "${current_prefix}." \
          'to_entries | map(select(.key | startswith($pfx) and (contains("-") | not))) | sort_by(.value) | reverse[] | .key' 2>/dev/null)
        if [[ -n "$fallback_tgt" ]]; then
          if [[ "$(printf '%s\n%s' "$current" "$fallback_tgt" | sort -V | tail -1)" == "$fallback_tgt" ]]; then
            local blocked_ver="$tgt"
            tgt="$fallback_tgt"
            note2="${blocked_ver} cooldown ($(format_duration "$age"))"
          else
            R_PKG+=("$pkg"); R_FROM+=("$current"); R_TO+=("$tgt"); R_AGE+=("$age")
            continue
          fi
        else
          R_PKG+=("$pkg"); R_FROM+=("$current"); R_TO+=("$tgt"); R_AGE+=("$age")
          continue
        fi
      fi
    fi

    P_PKG+=("$pkg"); P_FROM+=("$current"); P_TO+=("$tgt")
    P_NOTE+=("$note"); P_NOTE2+=("${note2:-}"); P_LOC+=("$loc"); P_MAJOR+=("$is_major")
  done < <(echo "$OUTDATED" | jq -r 'keys[]')

  # ── Display ─────────────────────────────────────────────────────────────────
  _fmt_loc() {
    local loc="$1"
    if   [[ "$loc" == "catalog" ]]; then echo "[catalog]"
    elif [[ "$loc" == "unknown" ]]; then echo "[?]"
    elif [[ "${loc%%:*}" == "$ROOT/package.json" ]]; then echo "[root]"
    else echo "[$(basename "$(dirname "${loc%%:*}")")]"
    fi
  }

  declare -a C_IDX=() N_IDX=()
  for i in "${!P_PKG[@]}"; do
    [[ -z "${P_NOTE[$i]}" && -z "${P_NOTE2[$i]}" ]] && C_IDX+=("$i") || N_IDX+=("$i")
  done

  local has_updates=$(( ${#C_IDX[@]} + ${#N_IDX[@]} + ${#R_PKG[@]} ))
  if [[ $has_updates -gt 0 || ${#S_PKG[@]} -gt 0 ]]; then
    printf "\r\033[K\n"
    printf "  ${BOLD}%-38s  %-14s  %-14s  %s${RESET}\n" "Package" "Current" "Target" "Location"
    printf "  %s\n" "$(printf '%.0s─' {1..80})"
  fi

  for i in "${C_IDX[@]}"; do
    local clr=$GREEN; ${P_MAJOR[$i]} && clr=$YELLOW
    printf "  ${clr}%-38s${RESET}  %-14s  %-14s  ${DIM}%s${RESET}\n" \
      "${P_PKG[$i]}" "${P_FROM[$i]}" "${P_TO[$i]}" "$(_fmt_loc "${P_LOC[$i]}")"
  done

  if [[ ${#N_IDX[@]} -gt 0 ]]; then
    [[ ${#C_IDX[@]} -gt 0 ]] && echo ""
    [[ ${#C_IDX[@]} -gt 0 ]] && echo -e "  ${BOLD}Notable:${RESET}"
    for i in "${N_IDX[@]}"; do
      printf "  ${YELLOW}%-38s${RESET}  %-14s  %-14s  ${DIM}%s${RESET}\n" \
        "${P_PKG[$i]}" "${P_FROM[$i]}" "${P_TO[$i]}" "$(_fmt_loc "${P_LOC[$i]}")"
      [[ -n "${P_NOTE[$i]}"  ]] && printf "  ${DIM}  ↳ %s${RESET}\n" "${P_NOTE[$i]}"
      [[ -n "${P_NOTE2[$i]}" ]] && printf "  ${DIM}  ↳ %s${RESET}\n" "${P_NOTE2[$i]}"
    done
  fi

  if [[ ${#R_PKG[@]} -gt 0 ]]; then
    [[ $(( ${#C_IDX[@]} + ${#N_IDX[@]} )) -gt 0 ]] && echo ""
    echo -e "  ${BOLD}Too recent${RESET} ${DIM}(cooldown: $(format_duration "$COOLDOWN_MINUTES")):${RESET}"
    for i in "${!R_PKG[@]}"; do
      printf "  ${YELLOW}%-38s${RESET}  ${DIM}%s → %s  —  %s old${RESET}\n" \
        "${R_PKG[$i]}" "${R_FROM[$i]}" "${R_TO[$i]}" "$(format_duration "${R_AGE[$i]}")"
    done
  fi

  if [[ ${#S_PKG[@]} -gt 0 ]]; then
    [[ $has_updates -gt 0 ]] && echo ""
    echo -e "  ${BOLD}Skipped:${RESET}"
    for i in "${!S_PKG[@]}"; do
      printf "  ${YELLOW}%-38s${RESET}  ${DIM}%-14s  %-14s  %s${RESET}\n" \
        "${S_PKG[$i]}" "${S_FROM[$i]}" "${S_TO[$i]}" "$(_fmt_loc "${S_LOC[$i]}")"
    done
  fi

  echo ""

  if [[ ${#P_PKG[@]} -eq 0 ]]; then
    echo -e "${GREEN}✓ Nothing to update.${RESET}"; exit 0
  fi

  $DRY_RUN && { echo -e "${CYAN}Dry run — no changes made.${RESET}"; exit 0; }

  # ── Confirm ─────────────────────────────────────────────────────────────────
  if ! $AUTO_YES; then
    printf "Apply ${#P_PKG[@]} update(s)? [y/n] "
    IFS= read -r -s -n1 confirm
    echo ""
    if [[ "$confirm" == $'\e' || "$confirm" =~ ^[Nn]$ ]]; then
      echo "Aborted."; exit 0
    fi
    echo ""
  fi

  # ── Apply ────────────────────────────────────────────────────────────────────
  echo -e "${BOLD}Updating...${RESET}"

  for i in "${!P_PKG[@]}"; do
    local pkg="${P_PKG[$i]}"; local ver="${P_TO[$i]}"; local loc="${P_LOC[$i]}"

    if [[ "$loc" == "catalog" ]]; then
      _apply_catalog "$pkg" "$ver"
    elif [[ "$loc" != "unknown" ]]; then
      while IFS= read -r aloc; do
        [[ "$aloc" == "unknown" || "$aloc" == "catalog" ]] && continue
        local pkgjson="${aloc%%:*}"
        local prefix; prefix=$(PKG="$pkg" perl -ne 'if (m|^\s+"\Q$ENV{PKG}\E"\s*:\s*"([^0-9v"]*)v?\d+\.|) { print $1; exit }' "$pkgjson")
        PKG="$pkg" PREFIX="$prefix" VER="$ver" perl -i -pe 's|^(\s+"\Q$ENV{PKG}\E"\s*:\s*")[^0-9v"]*v?\d+\.[^"]*"|${1}$ENV{PREFIX}$ENV{VER}"|' "$pkgjson"
        echo -e "  ${CYAN}${pkgjson#$ROOT/}${RESET}  ${pkg}  →  ${prefix}${ver}"
      done < <(find_all_locations "$pkg")
    else
      echo -e "  ${YELLOW}⚠ Could not locate ${pkg} — skipped${RESET}"
    fi
  done

  echo ""
  _pm_install
  echo ""
  echo -e "${GREEN}✓ Done! ${#P_PKG[@]} package(s) updated.${RESET}"
}
