#!/usr/bin/env bash
# pnpm-update
# Like `pnpm outdated` + `pnpm update`, but skips major version bumps by default.
# For packages where only a major bump exists, updates to latest within current major.
# Respects minimumReleaseAge / minimumReleaseAgeExclude from pnpm-workspace.yaml or .npmrc.
# Handles pnpm workspaces and catalog: entries.
#
# Usage: pnpm-update [--dry-run] [-y] [--force] [--major] [--cooldown AGE] [--actions] [<package>]
#   <package>          Only update this package (or this action with --actions)
#   --dry-run          Show what would change, make no changes
#   -y / --yes         Skip confirmation prompt
#   --force            Check all exact pins directly via npm
#   --major            Allow major version bumps
#   --cooldown AGE  Override cooldown (e.g. 24h, 7d; 0d disables)
#   --actions          Update GitHub Actions in workflows instead of packages (pnpm 12+)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034  # read by _update-lib.sh
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

_find_additional_update_locations() {
  local pkg="$1"
  pnpm_override_contains_package "$pkg" && echo "override"
}

_apply_catalog() {
  local pkg="$1" ver="$2"
  pnpm_apply_catalog_version "$pkg" "$ver"
  echo -e "  ${CYAN}catalog${RESET}              ${pkg}  ->  ${ver}"
}

_apply_extra_update_location() {
  local loc="$1" pkg="$2" ver="$3"
  [[ "$loc" == "override" ]] || return 1
  pnpm_apply_override_version "$pkg" "$ver"
  echo -e "  ${CYAN}overrides${RESET}            ${pkg}  ->  ${ver}"
}

_pm_install() {
  out_section "pnpm install"
  pnpm install
}

# ── GitHub Actions (--actions) ────────────────────────────────────────────────
# pnpm finds and pins the actions itself, honouring minimumReleaseAge from
# 12.7.0; this wrapper only chooses the targets and shows them, like the
# package plan.
_update_actions() {
  local version
  version=$(pnpm --version 2>/dev/null || true)
  if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]] || semver_newer 12.7.0 "${BASH_REMATCH[0]}"; then
    echo -e "${RED}Error: --actions requires pnpm 12.7 or newer, which applies the release-age cooldown to actions (found ${version:-none})${RESET}" >&2
    exit 1
  fi
  $DRY_RUN && out_dry_run
  out_context "pnpm" "GitHub Actions"

  local json rows errors
  errors=$(mktemp)
  out_progress "Checking GitHub Actions…"
  # Hold pnpm's diagnostics until the progress line is cleared, so they don't print onto it.
  json=$(pnpm outdated --include-github-actions --json 2>"$errors" || true)
  out_progress_done
  cat "$errors" >&2
  rm -f "$errors"
  [[ -z "$json" ]] && json="{}"
  rows=$(printf '%s' "$json" | jq -r --arg major "$ALLOW_MAJOR" --arg only "$FILTER_PKG" '
    if type == "object" then to_entries else [] end
    | map(select(.value.dependencyType == "githubAction")
        | select($only == "" or .key == $only)
        | .value as $v
        | (if $major == "true" then $v.latest else $v.wanted end) as $target
        | [.key, ($v.current // ""), ($target // ""), ($v.latest // "")])
    | .[] | @tsv') || { echo -e "${RED}Error: could not read pnpm outdated output${RESET}" >&2; exit 1; }

  local -a names=() held=()
  local name current target latest
  if [[ -n "$rows" ]]; then
    out_section "Actions"
    out_table_header "%-38s  %-14s  %s" "Action" "Current" "Target"
  fi
  while IFS=$'\t' read -r name current target latest; do
    [[ -z "$name" ]] && continue
    if [[ -n "$target" && "$target" != "$current" ]]; then
      names+=("$name")
      printf "  ${GREEN}%-38s${RESET}  %-14s  %s\n" "$name" "$current" "$target"
    fi
    if [[ "$ALLOW_MAJOR" == false && -n "$latest" && "$latest" != "${target:-$current}" ]]; then
      held+=("$name $current → $latest")
    fi
  done <<< "$rows"
  if [[ ${#held[@]} -gt 0 ]]; then
    out_section "New majors (use --major)"
    printf "  ${DIM}%s${RESET}\n" "${held[@]}"
  fi
  echo ""

  if [[ ${#names[@]} -eq 0 ]]; then
    echo "  $OUT_OK Nothing to update."; exit 0
  fi
  $DRY_RUN && exit 0
  confirm_apply "Apply ${#names[@]} update(s)?" "$AUTO_YES" || exit 0

  local -a latest_flag=()
  [[ "$ALLOW_MAJOR" == true ]] && latest_flag=(--latest)
  pnpm update --include-github-actions ${latest_flag[@]+"${latest_flag[@]}"} "${names[@]}"
  echo "  $OUT_OK Done! ${#names[@]} action(s) pinned to commit hashes."
  exit 0
}
[[ "$ACTIONS" == true ]] && _update_actions

# ── Fetch outdated packages ───────────────────────────────────────────────────
RECURSIVE_FLAG=""
[[ -f "$ROOT/pnpm-workspace.yaml" ]] && RECURSIVE_FLAG="--recursive"

print_header
out_progress "Checking for outdated packages…"
OUTDATED=$(pnpm outdated $RECURSIVE_FLAG --json 2>/dev/null || true)
[[ -z "$OUTDATED" ]] && OUTDATED="{}"
# pnpm 11.28+ keys a package found at several versions or dependency types as
# "name@version (dev)"; collapse to one entry per name, keeping the oldest current.
# pnpm outdated reports "wanted" and "latest" but not "current".
# For this wrapper, "wanted" is the relevant baseline for update planning.
OUTDATED=$(echo "$OUTDATED" | jq '
  def base_name: sub(" \\([^)]*\\)$"; "") | if test("^@?[^@]+@[^@]+$") then sub("@[^@]+$"; "") else . end;
  def version_key: (.current // .wanted // "") | split("-")[0] | split(".") | map(tonumber? // 0);
  if type == "object" then
    to_entries
    # A project with update.githubActions set reports workflow actions too.
    | map(select(.value.dependencyType != "githubAction"))
    | map(.key |= base_name)
    | group_by(.key)
    | map(min_by(.value | version_key))
    | from_entries
    | with_entries(
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
