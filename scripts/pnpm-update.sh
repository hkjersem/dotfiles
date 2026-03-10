#!/usr/bin/env bash
# pnpm-update
# Like `pnpm outdated` + `pnpm update`, but skips major version bumps.
# For packages where only a major bump exists, updates to latest within current major.
# Respects minimumReleaseAge / minimumReleaseAgeExclude from pnpm-workspace.yaml or .npmrc.
# Handles pnpm workspaces and catalog: entries.
#
# Usage: pnpm-update [--dry-run] [-y] [--cooldown <days>]
#   --dry-run          Show what would change, make no changes
#   -y / --yes         Skip confirmation prompt
#   --cooldown <days>  Override cooldown in days (0 = disable)

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi

# ── Args ──────────────────────────────────────────────────────────────────────
DRY_RUN=false
AUTO_YES=false
CLI_COOLDOWN=""   # explicitly set via flag; "0" disables cooldown

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n)  DRY_RUN=true; shift ;;
    --yes|-y)      AUTO_YES=true; shift ;;
    --cooldown)
      if [[ $# -lt 2 ]]; then
        echo -e "${RED}Error: --cooldown requires a value${RESET}" >&2; exit 1
      fi
      CLI_COOLDOWN="$2"; shift 2 ;;
    --cooldown=*)  CLI_COOLDOWN="${1#*=}"; shift ;;
    --help|-h)
      echo "Usage: pnpm-update [--dry-run] [--yes] [--cooldown <days>]"
      echo "  --dry-run         Show planned updates without making changes"
      echo "  --yes             Skip confirmation prompt"
      echo "  --cooldown <days> Override cooldown in days (0 = disable)"
      exit 0 ;;
    *) shift ;;
  esac
done

# ── Requirements ──────────────────────────────────────────────────────────────
for cmd in jq pnpm npm node perl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}Error: '$cmd' is required.${RESET}" >&2; exit 1; }
done

# ── Find project root ─────────────────────────────────────────────────────────
ROOT="$PWD"
_d="$PWD"
while [[ "$_d" != "/" ]]; do
  if [[ -f "$_d/pnpm-lock.yaml" ]]; then ROOT="$_d"; break; fi
  _d="$(dirname "$_d")"
done
cd "$ROOT"
[[ -f "package.json" ]] || { echo -e "${RED}No package.json found in $ROOT${RESET}" >&2; exit 1; }

# ── Cooldown + exclude detection ──────────────────────────────────────────────
# Priority for cooldown value:  --cooldown flag (days) > pnpm-workspace.yaml (minutes) > .npmrc (minutes)
# Excludes are always merged from all sources (workspace file + .npmrc).

COOLDOWN_MINUTES=""
COOLDOWN_SOURCE=""
COOLDOWN_EXCLUDE=()

if [[ -n "$CLI_COOLDOWN" ]]; then
  if ! [[ "$CLI_COOLDOWN" =~ ^[0-9]+$ ]]; then
    echo -e "${RED}Error: --cooldown value must be a non-negative integer (days)${RESET}" >&2; exit 1
  fi
  if [[ "$CLI_COOLDOWN" -eq 0 ]]; then
    COOLDOWN_MINUTES="0"   # explicit disable
  else
    COOLDOWN_MINUTES=$(( CLI_COOLDOWN * 1440 ))   # days → minutes
  fi
  COOLDOWN_SOURCE="--cooldown flag"
fi

# Helper: strip a YAML list item to its bare value
_yaml_strip() { echo "$1" | sed "s/^[[:space:]]*-[[:space:]]*//;s/^['\"]//;s/['\"]$//;s/[[:space:]]*$//"; }

# pnpm-workspace.yaml
if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
  if [[ -z "$COOLDOWN_MINUTES" ]]; then
    pws_age=$(grep -E '^minimumReleaseAge[[:space:]]*:' "$ROOT/pnpm-workspace.yaml" \
              | grep -oE '[0-9]+' | head -1 || true)
    if [[ -n "$pws_age" ]]; then
      COOLDOWN_MINUTES="$pws_age"
      COOLDOWN_SOURCE="pnpm-workspace.yaml"
    fi
  fi
  # Only honour excludes when cooldown is not explicitly overridden via flag
  if [[ -z "$CLI_COOLDOWN" ]]; then
    while IFS= read -r _line; do
      _val=$(_yaml_strip "$_line")
      [[ -n "$_val" ]] && COOLDOWN_EXCLUDE+=("$_val")
    done < <(awk '/^minimumReleaseAgeExclude:/{f=1;next} /^[a-zA-Z]/{f=0} f && /^[[:space:]]+-/' \
                 "$ROOT/pnpm-workspace.yaml" 2>/dev/null || true)
  fi
fi

# .npmrc
if [[ -f "$ROOT/.npmrc" ]]; then
  if [[ -z "$COOLDOWN_MINUTES" ]]; then
    npmrc_age=$(grep -E '^minimum-release-age[[:space:]]*=' "$ROOT/.npmrc" \
                | grep -oE '[0-9]+' | head -1 || true)
    if [[ -n "$npmrc_age" ]]; then
      COOLDOWN_MINUTES="$npmrc_age"
      COOLDOWN_SOURCE=".npmrc"
    fi
  fi
  # Only honour excludes when cooldown is not explicitly overridden via flag
  if [[ -z "$CLI_COOLDOWN" ]]; then
    # Array style:  minimum-release-age-exclude[]=pattern
    while IFS= read -r _line; do
      _val=$(echo "$_line" | sed 's/^minimum-release-age-exclude\[\][[:space:]]*=[[:space:]]*//')
      [[ -n "$_val" ]] && COOLDOWN_EXCLUDE+=("$_val")
    done < <(grep -E '^minimum-release-age-exclude\[\]' "$ROOT/.npmrc" 2>/dev/null || true)
    # Inline style:  minimum-release-age-exclude=pkg1 pkg2
    _inline=$(grep -E '^minimum-release-age-exclude[[:space:]]*=' "$ROOT/.npmrc" 2>/dev/null \
              | head -1 | sed 's/^minimum-release-age-exclude[[:space:]]*=[[:space:]]*//' || true)
    if [[ -n "$_inline" ]]; then
      read -ra _parts <<< "$_inline"
      COOLDOWN_EXCLUDE+=("${_parts[@]}")
    fi
  fi
fi

# ── Semver helpers ────────────────────────────────────────────────────────────
# Returns the "breaking" version prefix under caret-range semver rules:
#   4.1.2  → "4"      major > 0: minor/patch bump freely
#   0.4.26 → "0.4"    major = 0: minor is breaking
#   0.0.5  → "0.0.5"  major = minor = 0: every change is breaking
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
  # Only show minutes when there are no days (sub-hour precision only matters for short durations)
  [[ $days -eq 0 && $rem -gt 0 ]] && out="${out:+$out }${rem}m"
  echo "${out:-0m}"
}

# Returns publish age of pkg@ver in minutes, or empty string on failure
# Optionally pass pre-fetched time JSON as $3 to avoid extra npm calls
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

# Supports glob patterns (e.g. @scope/*) and pkg@version entries (version part ignored)
is_cooldown_excluded() {
  local pkg="$1" entry name_part
  for entry in "${COOLDOWN_EXCLUDE[@]}"; do
    if [[ "$entry" == @* ]]; then
      # Scoped: @scope/name or @scope/name@version — strip version suffix
      [[ "$entry" =~ ^(@[^/]+/[^@]+) ]] && name_part="${BASH_REMATCH[1]}" || name_part="$entry"
    else
      name_part="${entry%%@*}"
    fi
    # Unquoted $name_part enables bash glob matching (e.g. @navikt/*)
    [[ "$pkg" == $name_part ]] && return 0
  done
  return 1
}

# ── Detect where a package version lives ─────────────────────────────────────
# Returns "catalog" or "<absolute-path-to-package.json>:<depType>"
find_location() {
  local pkg="$1"

  # Check catalog block in pnpm-workspace.yaml first
  if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
    if awk '/^catalog:/{f=1;next} /^[a-zA-Z]/{f=0} f' "$ROOT/pnpm-workspace.yaml" \
        | grep -qE "^\s+['\"]?$(echo "$pkg" | sed 's/[.+*?[\^${}|()]/\\&/g')['\"]?\s*:"; then
      echo "catalog"; return
    fi
  fi

  # Build list of package.json files: root first, then workspace members
  local pkgjsons=("$ROOT/package.json")
  if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
    while IFS= read -r pattern; do
      [[ -z "$pattern" ]] && continue
      for _wd in $ROOT/$pattern; do
        [[ -f "$_wd/package.json" ]] && pkgjsons+=("$_wd/package.json")
      done
    done < <(awk '/^packages:/{f=1;next} /^[a-zA-Z]/{f=0} f && /^[[:space:]]+-/' \
                 "$ROOT/pnpm-workspace.yaml" \
             | sed "s/^[[:space:]]*-[[:space:]]*//;s/['\"]//g;s/[[:space:]]*$//")
  fi

  # Find the most specific declaration (skip loose ranges, use as fallback)
  local fallback="" pkgjson deptype val
  for pkgjson in "${pkgjsons[@]}"; do
    for deptype in dependencies devDependencies peerDependencies optionalDependencies; do
      val=$(jq -r ".${deptype}[\"${pkg}\"] // empty" "$pkgjson" 2>/dev/null) || continue
      [[ -z "$val" ]] && continue
      [[ "$val" == catalog* ]] && echo "catalog" && return
      # Loose ranges (>=, *) are low-priority — keep searching for a pinned declaration
      if [[ "$val" =~ ^">" || "$val" == "*" ]]; then
        [[ -z "$fallback" ]] && fallback="$pkgjson:$deptype"
        continue
      fi
      echo "$pkgjson:$deptype"; return
    done
  done
  [[ -n "$fallback" ]] && echo "$fallback" || echo "unknown"
}

# ── Header ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}Checking for outdated packages...${RESET}"

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

# ── Get outdated packages ─────────────────────────────────────────────────────
RECURSIVE_FLAG=""
[[ -f "$ROOT/pnpm-workspace.yaml" ]] && RECURSIVE_FLAG="--recursive"

OUTDATED=$(pnpm outdated $RECURSIVE_FLAG --json 2>/dev/null || true)
if [[ -z "$OUTDATED" || "$OUTDATED" == "{}" ]]; then
  echo -e "${GREEN}✓ All packages are up to date!${RESET}"; exit 0
fi

# ── Build update plan ─────────────────────────────────────────────────────────
declare -a P_PKG=() P_FROM=() P_TO=() P_NOTE=() P_NOTE2=() P_LOC=()   # to update
declare -a S_PKG=() S_FROM=() S_TO=() S_LOC=()                # skipped (major-only)
declare -a R_PKG=() R_FROM=() R_TO=() R_AGE=()              # too recent

COOLDOWN_ACTIVE=false
[[ -n "$COOLDOWN_MINUTES" && "$COOLDOWN_MINUTES" -gt 0 ]] && COOLDOWN_ACTIVE=true

while IFS= read -r pkg; do
  current=$(echo "$OUTDATED" | jq -r ".\"${pkg}\".current")
  latest=$(echo "$OUTDATED"  | jq -r ".\"${pkg}\".latest")
  [[ "$current" == "null" || "$latest" == "null" ]] && continue

  current_prefix=$(breaking_prefix "$current")
  latest_prefix=$(breaking_prefix "$latest")
  loc=$(find_location "$pkg")

  # Skip loose-range peer deps (e.g. peerDependency: ">=5.x") — no pin to update
  if [[ "$loc" != "catalog" && "$loc" != "unknown" ]]; then
    declared=$(jq -r ".${loc##*:}[\"${pkg}\"] // empty" "${loc%%:*}" 2>/dev/null || true)
    [[ "$declared" =~ ^">" || "$declared" == "*" ]] && continue
  fi

  # Determine target version
  tgt="" note="" note2=""
  if [[ "$current" == *-* ]]; then
    # Current is a prerelease — find latest prerelease within same major
    current_major=$(echo "$current" | cut -d. -f1)
    safe=$(latest_prerelease_in_major "$pkg" "$current_major")
    if [[ -n "$safe" && "$safe" != "$current" && \
          "$(printf '%s\n%s' "$current" "$safe" | sort -V | tail -1)" == "$safe" ]]; then
      tgt="$safe"; note="prerelease"
    else
      continue  # already on latest prerelease, nothing to do
    fi
  elif [[ "$latest_prefix" != "$current_prefix" ]]; then
    safe=$(latest_in_prefix "$pkg" "$current_prefix")
    if [[ -n "$safe" && "$safe" != "$current" ]]; then
      # Guard against npm returning an older version than what's installed
      if [[ "$(printf '%s\n%s' "$current" "$safe" | sort -V | tail -1)" != "$safe" ]]; then
        S_PKG+=("$pkg"); S_FROM+=("$current"); S_TO+=("$latest"); S_LOC+=("$loc")
        continue
      fi
      tgt="$safe"; note="${latest} latest"
    else
      S_PKG+=("$pkg"); S_FROM+=("$current"); S_TO+=("$latest"); S_LOC+=("$loc")
      continue
    fi
  else
    [[ "$latest" == "$current" ]] && continue   # already up to date
    tgt="$latest"
  fi

  # Cooldown age check (skip if package is in exclude list)
  if $COOLDOWN_ACTIVE && ! is_cooldown_excluded "$pkg"; then
    echo -ne "  ${DIM}Checking age of ${pkg}@${tgt}...${RESET}\r" >&2
    pkg_times=$(npm view "$pkg" time --json 2>/dev/null) || pkg_times=""
    age=$(version_age_minutes "$pkg" "$tgt" "$pkg_times") || age=""
    echo -ne "\033[2K"
    if [[ -n "$age" && "$age" -lt "$COOLDOWN_MINUTES" ]]; then
      # Target too recent — walk back through versions (newest first) to find one that passes cooldown
      fallback_tgt=""
      while IFS= read -r candidate; do
        [[ "$candidate" == "$current" ]] && break
        cand_age=$(version_age_minutes "$pkg" "$candidate" "$pkg_times") || continue
        if [[ -n "$cand_age" && "$cand_age" -ge "$COOLDOWN_MINUTES" ]]; then
          fallback_tgt="$candidate"; break
        fi
      done < <(echo "$pkg_times" | jq -r --arg pfx "${current_prefix}." 'to_entries | map(select(.key | startswith($pfx) and (contains("-") | not))) | sort_by(.value) | reverse[] | .key' 2>/dev/null)
      if [[ -n "$fallback_tgt" ]]; then
        # Only use fallback if it's actually newer than current
        if [[ "$(printf '%s\n%s' "$current" "$fallback_tgt" | sort -V | tail -1)" == "$fallback_tgt" ]]; then
          blocked_ver="$tgt"
          tgt="$fallback_tgt"
          note2="${blocked_ver} cooldown ($(format_duration "$age"))"
          # If there's already a note (e.g. newer major), keep it as note, cooldown as note2
          # note stays as-is (major latest), note2 is cooldown
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

  P_PKG+=("$pkg"); P_FROM+=("$current"); P_TO+=("$tgt"); P_NOTE+=("${note2:-}"); P_NOTE2+=("$note"); P_LOC+=("$loc")
done < <(echo "$OUTDATED" | jq -r 'keys[]')

# ── Display ───────────────────────────────────────────────────────────────────

# Helper: format location string
_fmt_loc() {
  local loc="$1"
  if   [[ "$loc" == "catalog" ]]; then echo "[catalog]"
  elif [[ "$loc" == "unknown" ]]; then echo "[?]"
  else echo "[$(basename "$(dirname "${loc%%:*}")")]"
  fi
}

# Split P_ arrays into clean (no notes) and notable (has notes)
declare -a C_IDX=() N_IDX=()
for i in "${!P_PKG[@]}"; do
  [[ -z "${P_NOTE[$i]}" && -z "${P_NOTE2[$i]}" ]] && C_IDX+=("$i") || N_IDX+=("$i")
done

has_updates=$(( ${#C_IDX[@]} + ${#N_IDX[@]} + ${#R_PKG[@]} ))
if [[ $has_updates -gt 0 || ${#S_PKG[@]} -gt 0 ]]; then
  printf "\r\033[K\n"  # clear any lingering progress line, then blank line
  printf "  ${BOLD}%-38s  %-14s  %-14s  %s${RESET}\n" \
    "Package" "Current" "Target" "Location"
  printf "  %s\n" "$(printf '%.0s─' {1..80})"
fi

# Clean updates (no notes) — no section title, directly under header
for i in "${C_IDX[@]}"; do
  printf "  ${GREEN}%-38s${RESET}  %-14s  %-14s  ${DIM}%s${RESET}\n" \
    "${P_PKG[$i]}" "${P_FROM[$i]}" "${P_TO[$i]}" "$(_fmt_loc "${P_LOC[$i]}")"
done

# Notable updates (have notes)
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
      "${R_PKG[$i]}" "${R_FROM[$i]}" "${R_TO[$i]}" \
      "$(format_duration "${R_AGE[$i]}")"
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

# ── Confirm ───────────────────────────────────────────────────────────────────
if ! $AUTO_YES; then
  printf "Apply ${#P_PKG[@]} update(s)? [y/n] "
  IFS= read -r -s -n1 confirm
  echo ""
  if [[ "$confirm" == $'\e' || "$confirm" =~ ^[Nn]$ ]]; then
    echo "Aborted."; exit 0
  fi
  echo ""
fi

# ── Apply changes ─────────────────────────────────────────────────────────────
echo -e "${BOLD}Updating...${RESET}"

for i in "${!P_PKG[@]}"; do
  pkg="${P_PKG[$i]}"; ver="${P_TO[$i]}"; loc="${P_LOC[$i]}"

  if [[ "$loc" == "catalog" ]]; then
    # Use env vars so perl sees single-quoted regex — avoids @scope being interpolated as a perl array
    PKG="$pkg" VER="$ver" perl -i -pe "s|^(\s+['\"]?\Q\$ENV{PKG}\E['\"]?\s*:\s*).*|\${1}\$ENV{VER}|" "$ROOT/pnpm-workspace.yaml"
    echo -e "  ${CYAN}catalog${RESET}              ${pkg}  →  ${ver}"
  elif [[ "$loc" != "unknown" ]]; then
    pkgjson="${loc%%:*}"; deptype="${loc##*:}"
    # Read current prefix (^, ~ or empty) via env var to avoid quoting issues
    prefix=$(PKG="$pkg" perl -ne 'if (m|^\s+"\Q$ENV{PKG}\E"\s*:\s*"([^0-9"]*)|) { print $1; exit }' "$pkgjson")
    # Replace the version value in-place, preserving all surrounding formatting
    PKG="$pkg" PREFIX="$prefix" VER="$ver" perl -i -pe 's|^(\s+"\Q$ENV{PKG}\E"\s*:\s*")[^"]*"|${1}$ENV{PREFIX}$ENV{VER}"|' "$pkgjson"
    echo -e "  ${CYAN}${pkgjson#$ROOT/}${RESET}  ${pkg}  →  ${prefix}${ver}"
  else
    echo -e "  ${YELLOW}⚠ Could not locate ${pkg} — skipped${RESET}"
  fi
done

echo ""
echo -e "${BOLD}Running pnpm install...${RESET}"
pnpm install

echo ""
echo -e "${GREEN}✓ Done! ${#P_PKG[@]} package(s) updated.${RESET}"
