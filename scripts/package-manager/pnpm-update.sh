#!/usr/bin/env bash
# pnpm-update
# Like `pnpm outdated` + `pnpm update`, but skips major version bumps by default.
# For packages where only a major bump exists, updates to latest within current major.
# Respects minimumReleaseAge / minimumReleaseAgeExclude from pnpm-workspace.yaml or .npmrc.
# Handles pnpm workspaces and catalog: entries.
#
# Usage: pnpm-update [--dry-run] [-y] [--force] [--major] [--cooldown <days>] [<package>]
#   <package>          Only update this package
#   --dry-run          Show what would change, make no changes
#   -y / --yes         Skip confirmation prompt
#   --force            Check all exact pins directly via npm
#   --major            Allow major version bumps
#   --cooldown <days>  Override cooldown in days (0 = disable)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PM_NAME="pnpm"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

# ── Args ──────────────────────────────────────────────────────────────────────
_parse_args "$@"

# ── Requirements ──────────────────────────────────────────────────────────────
for cmd in jq pnpm npm node perl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}Error: '$cmd' is required.${RESET}" >&2; exit 1; }
done

# ── Find project root ─────────────────────────────────────────────────────────
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-}"
COOLDOWN_SOURCE="${COOLDOWN_SOURCE:-}"
COOLDOWN_EXCLUDE=()
CONFIG_RELEASE_AGE=""

_find_root "pnpm-lock.yaml"

# ── Cooldown config: pnpm-workspace.yaml (minutes) > .npmrc (minutes) ────────
if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
  if [[ -z "$COOLDOWN_MINUTES" ]]; then
    pws_age=$(grep -E '^minimumReleaseAge[[:space:]]*:' "$ROOT/pnpm-workspace.yaml" \
              | grep -oE '[0-9]+' | head -1 || true)
    if [[ -n "$pws_age" ]]; then
      COOLDOWN_MINUTES="$pws_age"
      COOLDOWN_SOURCE="pnpm-workspace.yaml"
    fi
  fi
  [[ -z "$CONFIG_RELEASE_AGE" ]] && CONFIG_RELEASE_AGE=$(grep -E '^minimumReleaseAge[[:space:]]*:' \
    "$ROOT/pnpm-workspace.yaml" | grep -oE '[0-9]+' | head -1 || true)
  if [[ -z "$CLI_COOLDOWN" ]]; then
    while IFS= read -r _line; do
      _val=$(_yaml_strip "$_line")
      [[ -n "$_val" ]] && COOLDOWN_EXCLUDE+=("$_val")
    done < <(awk '/^minimumReleaseAgeExclude:/{f=1;next} /^[a-zA-Z]/{f=0} f && /^[[:space:]]+-/' \
                 "$ROOT/pnpm-workspace.yaml" 2>/dev/null || true)
  fi
fi

if [[ -f "$ROOT/.npmrc" ]]; then
  if [[ -z "$COOLDOWN_MINUTES" ]]; then
    npmrc_age=$(grep -E '^minimum-release-age[[:space:]]*=' "$ROOT/.npmrc" \
                | grep -oE '[0-9]+' | head -1 || true)
    if [[ -n "$npmrc_age" ]]; then
      COOLDOWN_MINUTES="$npmrc_age"
      COOLDOWN_SOURCE=".npmrc"
    fi
  fi
  [[ -z "$CONFIG_RELEASE_AGE" ]] && CONFIG_RELEASE_AGE=$(grep -E '^minimum-release-age[[:space:]]*=' \
    "$ROOT/.npmrc" | grep -oE '[0-9]+' | head -1 || true)
  if [[ -z "$CLI_COOLDOWN" ]]; then
    while IFS= read -r _line; do
      _val=$(echo "$_line" | sed 's/^minimum-release-age-exclude\[\][[:space:]]*=[[:space:]]*//')
      [[ -n "$_val" ]] && COOLDOWN_EXCLUDE+=("$_val")
    done < <(grep -E '^minimum-release-age-exclude\[\]' "$ROOT/.npmrc" 2>/dev/null || true)
    _inline=$(grep -E '^minimum-release-age-exclude[[:space:]]*=' "$ROOT/.npmrc" 2>/dev/null \
              | head -1 | sed 's/^minimum-release-age-exclude[[:space:]]*=[[:space:]]*//' || true)
    if [[ -n "$_inline" ]]; then
      read -ra _parts <<< "$_inline"
      COOLDOWN_EXCLUDE+=("${_parts[@]}")
    fi
  fi
fi

# ── pnpm hooks ────────────────────────────────────────────────────────────────
_find_location_extra() {
  local pkg="$1"
  if [[ -f "$ROOT/pnpm-workspace.yaml" ]]; then
    if awk '/^catalog:/{f=1;next} /^[a-zA-Z]/{f=0} f' "$ROOT/pnpm-workspace.yaml" \
        | grep -qE "^\s+['\"]?$(echo "$pkg" | sed 's/[.+*?[\^${}|()]/\\&/g')['\"]?\s*:"; then
      echo "catalog"; return
    fi
  fi
  echo ""
}

_apply_catalog() {
  local pkg="$1" ver="$2"
  PKG="$pkg" VER="$ver" perl -i -pe \
    's|^(\s+['"'"'"]?\Q$ENV{PKG}\E['"'"'"]?\s*:\s*)(['"'"'"]?)v?[^'"'"'"\r\n]*\2|${1}${2}$ENV{VER}${2}|' \
    "$ROOT/pnpm-workspace.yaml"
  echo -e "  ${CYAN}catalog${RESET}              ${pkg}  ->  ${ver}"
}

_pm_install() {
  echo -e "${BOLD}Running pnpm install...${RESET}"
  pnpm install
}

# ── Fetch outdated packages ───────────────────────────────────────────────────
RECURSIVE_FLAG=""
[[ -f "$ROOT/pnpm-workspace.yaml" ]] && RECURSIVE_FLAG="--recursive"

OUTDATED=$(pnpm outdated $RECURSIVE_FLAG --json 2>/dev/null || true)
[[ -z "$OUTDATED" ]] && OUTDATED="{}"

_augment_outdated

# ── Run ───────────────────────────────────────────────────────────────────────
run_plan
