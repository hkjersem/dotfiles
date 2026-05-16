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
cd "$ROOT"

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
  read_npmrc_release_age "$ROOT/.npmrc" minutes minimum-release-age
  [[ -z "$CLI_COOLDOWN" ]] && read_npmrc_exclude "$ROOT/.npmrc" minimum-release-age-exclude
fi

# ── pnpm hooks ────────────────────────────────────────────────────────────────
_find_location_extra() {
  local pkg="$1"
  pnpm_catalog_contains_package "$pkg" && { echo "catalog"; return; }
  echo ""
}

_apply_catalog() {
  local pkg="$1" ver="$2"
  pnpm_apply_catalog_version "$pkg" "$ver"
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
# pnpm outdated reports "wanted" and "latest" but not "current".
# For this wrapper, "wanted" is the relevant baseline for update planning.
OUTDATED=$(echo "$OUTDATED" | jq '
  if type == "object" then
    with_entries(
      .value |= if ((.current // "") == "") then
        . + { current: (.wanted // .latest // "") }
      else
        .
      end
    )
  else
    .
  end
' 2>/dev/null || echo "{}")

_augment_outdated

# ── Run ───────────────────────────────────────────────────────────────────────
run_plan
