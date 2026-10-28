#!/usr/bin/env bash
#
# prune-branches.sh — remove local branches whose remote branch was deleted,
# together with their clean linked worktrees, after confirmation.
#
# Usage: prune_branches [--dry-run | --yes] [--include-merged] [--no-fetch] [--depth N] [path]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/clean-targets.sh
source "$SCRIPT_DIR/lib/clean-targets.sh" || exit 1
# shellcheck source=lib/stale-branches.sh
source "$SCRIPT_DIR/lib/stale-branches.sh" || exit 1
# shellcheck source=package-manager/_update-lib.sh
source "$SCRIPT_DIR/package-manager/_update-lib.sh" || exit 1

usage() {
  cat <<EOF
Usage: prune_branches [options] [path]

Finds local branches whose upstream branch was deleted on its remote and
deletes them once their commits are confirmed on a remote (pull request head,
merged, rebased, or squash-merged). Clean linked worktrees for those branches
are removed first, wherever they are on disk. Branches that were never pushed
and repositories without a remote are never changed.

Shows a plan and asks [y/N] before deleting. --dry-run shows the plan only.
Fetching (git fetch --prune) still updates remote-tracking refs unless --no-fetch is given.

path defaults to the current directory. Inside a repository or worktree, only
that repository is checked; otherwise repositories are found recursively.

Options:
  -n, --dry-run    Show the plan without prompting or deleting
  --yes, -y        Skip the confirmation prompt
  --include-merged Also delete local branches already merged into the remote's
                   default branch whose remote branch still exists (the remote
                   branch is kept)
  --include-detached Also remove clean detached linked worktrees with remotely confirmed commits
  --older-than AGE Retain recent tips/worktrees (e.g. 24h, 7d); includes descendants outside .git
  --no-fetch       Use existing remote-tracking refs (requires --dry-run)
  --depth N        Directory depth to search for repositories (default: 4)
  --stale-after AGE Report never-pushed or untracked branches older than AGE (default: 90d)
  --jobs N         Repositories to fetch in parallel (default: 6)
  --timeout N      Seconds allowed per remote fetch (default: 60)
  -h, --help       Show this help
EOF
}

DRY_RUN=false
AUTO_YES=false
FETCH=true
DEPTH=4
JOBS=6
TARGET=""

need_number() {
  [[ "$2" =~ ^[0-9]+$ && "$2" -gt 0 ]] || { echo "$1 requires a positive number" >&2; exit 1; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=true; shift ;;
    --yes|-y) AUTO_YES=true; shift ;;
    --include-merged) SB_INCLUDE_MERGED=true; shift ;;
    --include-detached)
      # shellcheck disable=SC2034  # read by lib/stale-branches.sh
      SB_INCLUDE_DETACHED=true
      shift ;;
    --older-than)
      [[ $# -ge 2 && "$2" =~ ^[0-9]+[mhdw]$ ]] || { echo "Error: --older-than requires e.g. 24h, 7d or 2w." >&2; exit 2; }
      # shellcheck disable=SC2034  # read by lib/stale-branches.sh
      SB_OLDER_THAN=$2
      shift 2 ;;
    --no-fetch) FETCH=false; shift ;;
    --stale-after)
      [[ $# -ge 2 ]] || { echo "Error: --stale-after requires a duration." >&2; exit 2; }
      # shellcheck disable=SC2034  # read by lib/stale-branches.sh
      SB_STALE_SECONDS=$(parse_age_seconds "$2") || { echo "Error: --stale-after requires e.g. 7d or 2w." >&2; exit 2; }
      shift 2 ;;
    --depth|--jobs|--timeout)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 1; }
      need_number "$1" "$2"
      # shellcheck disable=SC2034  # SB_* settings are read by lib/stale-branches.sh
      case "$1" in
        --depth) DEPTH=$2 ;;
        --jobs) JOBS=$2 ;;
        --timeout) SB_FETCH_TIMEOUT=$2 ;;
      esac
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    *)
      [[ -z "$TARGET" ]] || { echo "Only one path may be given" >&2; exit 1; }
      TARGET=$1; shift ;;
  esac
done

if ! $DRY_RUN && ! $FETCH; then
  echo "--no-fetch requires --dry-run; deletion needs fresh remote data" >&2
  exit 1
fi
if $AUTO_YES && $DRY_RUN; then
  echo "--yes cannot be combined with --dry-run" >&2
  exit 1
fi
TARGET=${TARGET:-$PWD}
[[ -d "$TARGET" ]] || { echo "Not a directory: $TARGET" >&2; exit 1; }
TARGET=$(cd "$TARGET" && pwd -P) || exit 1

# shellcheck source=lib/output.sh
source "$SCRIPT_DIR/lib/output.sh" || exit 1

TMP=$(mktemp -d "${TMPDIR:-/tmp}/prune-branches.XXXXXX") || exit 1
trap 'rm -rf -- "$TMP"' EXIT
SB_TMP=$TMP
mkdir -p "$TMP/scratch"

# shellcheck disable=SC2034  # read by lib/stale-branches.sh
invoking_top=$(git rev-parse --show-toplevel 2>/dev/null) && SB_INVOKING_TOP=$(cd "$invoking_top" && pwd -P)

# ── Discover repositories ─────────────────────────────────────────────────────

REPOS=()
add_repo() {
  local common existing
  common=$(sb_common_dir "$1") || return 0
  for existing in ${REPOS[@]+"${REPOS[@]}"}; do
    [[ "$existing" == "$common" ]] && return 0
  done
  REPOS+=("$common")
}

if git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
  add_repo "$TARGET"
else
  prune_expr=()
  for name in "${CLEAN_DIR_TARGETS[@]}"; do
    [[ ${#prune_expr[@]} -gt 0 ]] && prune_expr+=(-o)
    prune_expr+=(-name "$name")
  done
  while IFS= read -r -d '' git_path; do
    dir=$(dirname "$git_path")
    # Submodules belong to their superproject and are not cleaned.
    [[ -n "$(git -C "$dir" rev-parse --show-superproject-working-tree 2>/dev/null)" ]] && continue
    add_repo "$dir"
  done < <(find "$TARGET" -maxdepth "$((DEPTH + 1))" \( -type d \( "${prune_expr[@]}" \) -prune \) \
    -o -name .git -prune -print0 2>/dev/null)
fi

if [[ ${#REPOS[@]} -eq 0 ]]; then
  out_note "No Git repositories found in $(sb_short_path "$TARGET")."
  exit 0
fi

$DRY_RUN && out_dry_run
context=("$(sb_short_path "$TARGET")" "${#REPOS[@]} repositor$( [[ ${#REPOS[@]} -eq 1 ]] && echo y || echo ies)")
$FETCH || context+=("No fetch: using remote-tracking refs from the last fetch")
out_context "${context[@]}"

repo_name() {
  if [[ "$(basename "$1")" == .git ]]; then sb_short_path "$(dirname "$1")"; else sb_short_path "$1"; fi
}

# ── Snapshot, fetch, classify ─────────────────────────────────────────────────

STATUS=()
N_CHECKED=0
for i in "${!REPOS[@]}"; do
  SB_GIT_DIR=${REPOS[$i]}
  if sb_has_remote; then
    STATUS+=(ok)
    if $FETCH; then
      sb_snapshot > "$TMP/$i.snapshot"
      sb_save_snapshot "$TMP/$i.snapshot"
    elif [[ -f "$SB_GIT_DIR/$SB_SNAPSHOT_FILE" ]]; then
      cp "$SB_GIT_DIR/$SB_SNAPSHOT_FILE" "$TMP/$i.snapshot"
    fi
  else
    STATUS+=(no-remote)
  fi
done

if $FETCH; then
  out_progress "Fetching remotes for ${#REPOS[@]} repositor$( [[ ${#REPOS[@]} -eq 1 ]] && echo y || echo ies)…"
  running=0
  for i in "${!REPOS[@]}"; do
    [[ "${STATUS[$i]}" == ok ]] || continue
    ( SB_GIT_DIR=${REPOS[$i]}; sb_fetch "$TMP/$i.requests" > "$TMP/$i.failed" 2> "$TMP/$i.errors" ) &
    running=$((running + 1))
    if [[ "$running" -ge "$JOBS" ]]; then wait; running=0; fi
  done
  wait
  out_progress_done
fi

for i in "${!REPOS[@]}"; do
  [[ "${STATUS[$i]}" == ok ]] || continue
  SB_GIT_DIR=${REPOS[$i]}
  SB_TMP=$TMP/scratch
  request_heads=""
  $FETCH && request_heads=$TMP/$i.requests
  sb_classify "$TMP/$i.snapshot" "$TMP/$i.failed" "$request_heads" > "$TMP/$i.plan"
  N_CHECKED=$((N_CHECKED + $(sb_git for-each-ref --format=x refs/heads 2>/dev/null | wc -l)))
done

# ── Report ────────────────────────────────────────────────────────────────────

N_DELETE=0; N_DELETE_WT=0; N_DETACHED=0; N_BLOCKED=0; N_REVIEW=0; N_UNVERIFIED=0; N_HINT=0; N_RECENT=0; N_UNPUSHED_WT=0
N_CLEAN=0; N_FETCH_FAILED=0; NO_REMOTE=()

print_kind() {
  local file=$1 kind=$2 icon=$3 k name sha detail wt
  while IFS="$SB_SEP" read -r k name sha detail wt; do
    [[ "$k" == "$kind" ]] || continue
    if [[ "$kind" == hint && "$name" == /* ]]; then
      printf '  %s %s\n    %s%s%s\n' "$icon" "$(sb_short_path "$name")" "$DIM" "$detail" "$RESET"
      continue
    fi
    if [[ "$detail" == *" (prune with "*")" ]]; then
      printf '  %s %-32s %s%s%s  %s %s%s%s\n' "$icon" "$name" "$DIM" "${sha:0:7}" "$RESET" \
        "${detail%% (prune with *}" "$DIM" "(prune with ${detail##* (prune with }" "$RESET"
    else
      printf '  %s %-32s %s%s%s  %s\n' "$icon" "$name" "$DIM" "${sha:0:7}" "$RESET" "$detail"
    fi
    [[ -n "$wt" ]] && printf '    %s└ worktree %s%s\n' "$DIM" "$(sb_short_path "$wt")" "$RESET"
  done < "$file"
}

count_kind() { awk -F "$SB_SEP" -v k="$2" '$1 == k { n++ } END { print n + 0 }' "$1"; }


for i in "${!REPOS[@]}"; do
  name=$(repo_name "${REPOS[$i]}")
  if [[ "${STATUS[$i]}" != ok ]]; then
    NO_REMOTE+=("$name")
    continue
  fi
  plan=$TMP/$i.plan
  failed=""
  [[ -s "$TMP/$i.failed" ]] && failed=$(tr '\n' ' ' < "$TMP/$i.failed")
  if [[ -s "$plan" ]]; then
    N_RECENT=$((N_RECENT + $(count_kind "$plan" recent)))
    N_UNPUSHED_WT=$((N_UNPUSHED_WT + $(count_kind "$plan" unpushed-detached)))
    awk -F "$SB_SEP" '$1 != "recent" && $1 != "unpushed-detached"' "$plan" > "$plan.kept" && mv "$plan.kept" "$plan"
  fi
  if [[ ! -s "$plan" && -z "$failed" ]]; then
    N_CLEAN=$((N_CLEAN + 1))
    continue
  fi
  out_section "$name"
  if [[ -n "$failed" ]]; then
    N_FETCH_FAILED=$((N_FETCH_FAILED + 1))
    echo "  $OUT_FAIL Fetch failed for: ${failed% } — branches tracking it were left alone"
    [[ -s "$TMP/$i.errors" ]] && sed -n '1,2s/^/    /p' "$TMP/$i.errors"
  fi
  print_kind "$plan" delete "${RED}−${RESET}"
  print_kind "$plan" delete-worktree "${RED}−${RESET}"
  print_kind "$plan" blocked "$OUT_WARN"
  print_kind "$plan" review "$OUT_WARN"
  print_kind "$plan" unverified "$OUT_WARN"
  print_kind "$plan" hint "$OUT_INFO"
  N_DELETE=$((N_DELETE + $(count_kind "$plan" delete)))
  N_DETACHED=$((N_DETACHED + $(count_kind "$plan" delete-worktree)))
  N_DELETE_WT=$((N_DELETE_WT + $(awk -F "$SB_SEP" '$1 == "delete" && $5 != "" { n++ } END { print n + 0 }' "$plan")))
  N_BLOCKED=$((N_BLOCKED + $(count_kind "$plan" blocked)))
  N_REVIEW=$((N_REVIEW + $(count_kind "$plan" review)))
  N_UNVERIFIED=$((N_UNVERIFIED + $(count_kind "$plan" unverified)))
  N_HINT=$((N_HINT + $(count_kind "$plan" hint)))
done

out_section "Summary"
N_WITH_REMOTE=$(( ${#REPOS[@]} - ${#NO_REMOTE[@]} ))
out_row "$OUT_INFO" "Checked" "$N_CHECKED local branch(es) in $N_WITH_REMOTE repositor$( [[ "$N_WITH_REMOTE" -eq 1 ]] && echo y || echo ies) with a remote"
if [[ "$N_DELETE" -gt 0 ]]; then
  detail="$N_DELETE branch(es)"
  [[ "$N_DELETE_WT" -gt 0 ]] && detail+=", $N_DELETE_WT with a worktree"
  out_row "${RED}−${RESET}" "To delete" "$detail"
fi
[[ "$N_DETACHED" -gt 0 ]] && out_row "${RED}−${RESET}" "Detached to remove" "$N_DETACHED worktree(s)"
[[ "$N_BLOCKED" -gt 0 ]] && out_row "$OUT_WARN" "Retained" "$N_BLOCKED (worktree safeguards or --older-than)"
[[ "$N_REVIEW" -gt 0 ]] && out_row "$OUT_WARN" "Needs review" "$N_REVIEW (upstream gone, but commits not confirmed on a remote)"
[[ "$N_UNVERIFIED" -gt 0 ]] && out_row "$OUT_WARN" "Unverified" "$N_UNVERIFIED (commits or remote data not confirmed)"
[[ "$N_HINT" -gt 0 ]] && out_row "$OUT_INFO" "Kept, looks stale" "$N_HINT (merged or old; see above)"
[[ "$N_RECENT" -gt 0 ]] && out_row "$OUT_INFO" "Not reported" "$N_RECENT unpushed branch(es) ${DIM}(lower --stale-after to list)${RESET}"
[[ "$N_UNPUSHED_WT" -gt 0 ]] && out_row "$OUT_INFO" "Not reported" "$N_UNPUSHED_WT detached worktree(s) with unpushed commits ${DIM}(--include-detached lists)${RESET}"
[[ "$N_CLEAN" -gt 0 ]] && out_row "$OUT_OK" "Nothing to report" "$N_CLEAN repositor$( [[ "$N_CLEAN" -eq 1 ]] && echo y || echo ies)"
if [[ ${#NO_REMOTE[@]} -gt 0 ]]; then
  list="${NO_REMOTE[0]}"
  for ((j = 1; j < ${#NO_REMOTE[@]} && j < 5; j++)); do list+=", ${NO_REMOTE[$j]}"; done
  [[ ${#NO_REMOTE[@]} -gt 5 ]] && list+=" (+$((${#NO_REMOTE[@]} - 5)) more)"
  out_row "${DIM}–${RESET}" "No remote (skipped)" "${#NO_REMOTE[@]}: $list"
fi
[[ "$N_FETCH_FAILED" -gt 0 ]] && out_row "$OUT_FAIL" "Fetch failed" "$N_FETCH_FAILED repositor$( [[ "$N_FETCH_FAILED" -eq 1 ]] && echo y || echo ies)"

STATUS_CODE=0
[[ "$N_FETCH_FAILED" -gt 0 ]] && STATUS_CODE=1

if [[ $((N_DELETE + N_DETACHED)) -eq 0 ]]; then
  echo ""
  if [[ "$SB_INCLUDE_MERGED" == true ]]; then
    out_note "Nothing to delete."
  else
    out_note "Nothing to delete. ${DIM}Only branches with a deleted remote branch are pruned; see --include-merged.${RESET}"
  fi
  exit "$STATUS_CODE"
fi
$DRY_RUN && exit "$STATUS_CODE"

# ── Apply ─────────────────────────────────────────────────────────────────────

echo ""
prompt="Delete $N_DELETE branch(es)"
[[ $((N_DELETE_WT + N_DETACHED)) -gt 0 ]] && prompt+=" and remove $((N_DELETE_WT + N_DETACHED)) worktree(s)"
confirm_apply "$prompt?" "$AUTO_YES" false || exit "$STATUS_CODE"

DELETED=0; REMOVED_WT=0; FAILED=0
for i in "${!REPOS[@]}"; do
  [[ "${STATUS[$i]}" == ok && -s "$TMP/$i.plan" ]] || continue
  SB_GIT_DIR=${REPOS[$i]}
  # shellcheck disable=SC2034  # read by lib/stale-branches.sh
  SB_TMP=$TMP/scratch
  name=$(repo_name "${REPOS[$i]}")
  while IFS="$SB_SEP" read -r kind branch sha detail wt; do
    case "$kind" in
      delete) action=(sb_apply_delete "$branch" "$sha" "$wt" "$detail") ;;
      delete-worktree) action=(sb_apply_detached "$wt" "$sha" "$detail" "$TMP/$i.requests") ;;
      *) continue ;;
    esac
    if error=$("${action[@]}" </dev/null); then
      [[ "$kind" != delete ]] || DELETED=$((DELETED + 1))
      [[ -n "$wt" ]] && REMOVED_WT=$((REMOVED_WT + 1))
      printf '  %s %s: %s %s(%s)%s\n' "$OUT_OK" "$name" "$branch" "$DIM" "${sha:0:12}" "$RESET"
    else
      FAILED=$((FAILED + 1))
      printf '  %s %s: %s — %s\n' "$OUT_FAIL" "$name" "$branch" "$(printf '%s' "$error" | head -1)"
    fi
  done < "$TMP/$i.plan"
done

echo ""
done_mark=$OUT_OK
[[ "$FAILED" -gt 0 ]] && done_mark=$OUT_FAIL
result="Deleted $DELETED branch(es)"
[[ "$REMOVED_WT" -gt 0 ]] && result+=", removed $REMOVED_WT worktree(s)"
[[ "$FAILED" -gt 0 ]] && result+=", $FAILED failed"
echo "  $done_mark $result."
if [[ "$DELETED" -gt 0 ]]; then
  out_note "${DIM}Restore a branch with: git branch <name> <commit> (each repository logs them in .git/prune-branches.log)${RESET}"
fi
[[ "$FAILED" -gt 0 ]] && STATUS_CODE=1
exit "$STATUS_CODE"
