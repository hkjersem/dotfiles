#!/usr/bin/env bash
# bun-update
# Like `bun outdated` + `bun update`, but skips major version bumps by default.
# Respects minimumReleaseAge from bunfig.toml (project) or ~/.bunfig.toml (global).
#
# Usage: bun-update [--dry-run] [-y] [--force] [--major] [--cooldown <days>] [<package>]
#   <package>          Only update this package
#   --dry-run          Show what would change, make no changes
#   -y / --yes         Skip confirmation prompt
#   --force            Re-check all exact-pinned packages
#   --major            Allow major version bumps
#   --cooldown <days>  Override cooldown in days (0 = disable)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PM_NAME="bun"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

# ── Args ──────────────────────────────────────────────────────────────────────
_parse_args "$@"

# ── Requirements ──────────────────────────────────────────────────────────────
for cmd in jq bun npm node perl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}Error: '$cmd' is required.${RESET}" >&2; exit 1; }
done

# ── Find project root ─────────────────────────────────────────────────────────
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-}"
COOLDOWN_SOURCE="${COOLDOWN_SOURCE:-}"
COOLDOWN_EXCLUDE=()
CONFIG_RELEASE_AGE=""

_find_root "bun.lockb"
cd "$ROOT"
# bun 1.7+ may use bun.lock (text format) instead — retry if root not resolved
if [[ ! -f "$ROOT/bun.lockb" && ! -f "$ROOT/bun.lock" ]]; then
  _find_root "bun.lock"
  cd "$ROOT"
fi

# ── Cooldown config: bunfig.toml (project) → ~/.bunfig.toml (global) ─────────
for _bunfig in "$ROOT/bunfig.toml" "$HOME/.bunfig.toml"; do
  [[ -f "$_bunfig" ]] || continue
  _bun_age_str=$(read_bunfig_mra "$_bunfig")
  if [[ -n "$_bun_age_str" ]]; then
    _bun_age_mins=$(parse_duration_minutes "$_bun_age_str")
    if [[ -n "$_bun_age_mins" ]]; then
      [[ -z "$CONFIG_RELEASE_AGE" ]] && CONFIG_RELEASE_AGE="$_bun_age_mins"
      if [[ -z "$COOLDOWN_MINUTES" ]]; then
        COOLDOWN_MINUTES="$_bun_age_mins"
        COOLDOWN_SOURCE="$_bunfig"
      fi
    fi
  fi
  [[ -n "$COOLDOWN_MINUTES" ]] && break
done
unset _bunfig _bun_age_str _bun_age_mins

# ── bun hooks ─────────────────────────────────────────────────────────────────
_find_location_extra() { echo ""; }
_apply_catalog()       { echo -e "  ${YELLOW}⚠ bun does not support catalogs${RESET}"; }
_pm_install() {
  echo -e "${BOLD}Running bun install...${RESET}"
  bun install
}

# ── Fetch outdated packages ───────────────────────────────────────────────────
# bun outdated uses plain ASCII pipes; format: | Package | Current | Update | Latest |
# Separator rows (|------|) are excluded by checking that cur starts with a digit.
_bun_raw=$(bun outdated 2>/dev/null || true)
OUTDATED=$(echo "$_bun_raw" | awk -F'|' '
  NF > 4 {
    pkg = $2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", pkg)
    cur = $3; gsub(/^[[:space:]]+|[[:space:]]+$/, "", cur)
    upd = $4; gsub(/^[[:space:]]+|[[:space:]]+$/, "", upd)
    lat = $5; gsub(/^[[:space:]]+|[[:space:]]+$/, "", lat)
    if (pkg ~ /^[@a-zA-Z]/ && cur ~ /^[0-9]/ && lat ~ /^[0-9]/)
      printf "{\"pkg\":\"%s\",\"current\":\"%s\",\"wanted\":\"%s\",\"latest\":\"%s\"}\n", pkg, cur, upd, lat
  }
' | jq -s '
  if length == 0 then {}
  else map({key: .pkg, value: {current: .current, wanted: .wanted, latest: .latest}}) | from_entries
  end
' 2>/dev/null || echo "{}")
unset _bun_raw

_augment_outdated

# ── Run ───────────────────────────────────────────────────────────────────────
run_plan
