#!/usr/bin/env bash
# npm-update
# Like `npm outdated` + `npm install`, but skips major version bumps by default.
# For packages where only a major bump exists, updates to latest within current major.
# Respects minimum-release-age from .npmrc (in days, unlike pnpm which uses minutes).
#
# Usage: npm-update [--dry-run] [-y] [--force] [--major] [--cooldown <days>] [<package>]
#   <package>          Only update this package
#   --dry-run          Show what would change, make no changes
#   -y / --yes         Skip confirmation prompt
#   --major            Allow major version bumps
#   --cooldown <days>  Override cooldown in days (0 = disable)
#   --force            Re-check exact-pinned packages that may be suppressed by minimum-release-age

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PM_NAME="npm"
AUGMENT_ALWAYS=true  # npm outdated --json omits exact-pinned packages where wanted === current
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

# ── Args ──────────────────────────────────────────────────────────────────────
_parse_args "$@"

# ── Requirements ──────────────────────────────────────────────────────────────
for cmd in jq npm node perl; do
  command -v "$cmd" &>/dev/null || { echo -e "${RED}Error: '$cmd' is required.${RESET}" >&2; exit 1; }
done

# ── Find project root ─────────────────────────────────────────────────────────
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-}"
COOLDOWN_SOURCE="${COOLDOWN_SOURCE:-}"
COOLDOWN_EXCLUDE=()
CONFIG_RELEASE_AGE=""

_find_root "package-lock.json"

# ── Cooldown config: project .npmrc → global ~/.npmrc (days → minutes) ─────────
# Supports both "min-release-age" (set by ensure-pm-config.sh) and "minimum-release-age"
for _npmrc_file in "$ROOT/.npmrc" "$HOME/.npmrc"; do
  [[ -f "$_npmrc_file" ]] || continue
  read_npmrc_release_age "$_npmrc_file" days
  [[ -z "$CLI_COOLDOWN" ]] && read_npmrc_exclude "$_npmrc_file"
  [[ -n "$COOLDOWN_MINUTES" ]] && break  # project takes precedence over global
done
unset _npmrc_file

# ── npm hooks ─────────────────────────────────────────────────────────────────
_pm_install() {
  echo -e "${BOLD}Running npm install...${RESET}"
  npm install
}

# ── Fetch outdated packages ───────────────────────────────────────────────────
# npm outdated --json exits 1 when packages are outdated; suppress with || true
# --workspaces ensures all workspace members are included.
# Shape: { "pkg": { "current": "...", "wanted": "...", "latest": "..." } }
# Same keys as lib expects — no normalization required
WORKSPACES_FLAG=""
if jq -e '.workspaces' "$ROOT/package.json" &>/dev/null; then
  WORKSPACES_FLAG="--workspaces"
fi
OUTDATED=$(npm outdated $WORKSPACES_FLAG --json 2>/dev/null || true)
[[ -z "$OUTDATED" ]] && OUTDATED="{}"

_augment_outdated

# ── Run ───────────────────────────────────────────────────────────────────────
run_plan
