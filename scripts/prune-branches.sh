#!/usr/bin/env bash
#
# prune-branches.sh — remove local branches whose remote branch was deleted,
# together with their clean linked worktrees. Dry run unless --apply is given.
#
# Usage: prune_branches [--apply [--yes]] [--include-merged] [--no-fetch] [--depth N] [path]

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

Without --apply nothing is deleted. Fetching (git fetch --prune) still updates
remote-tracking refs unless --no-fetch is given.

path defaults to the current directory. Inside a repository or worktree, only
that repository is checked; otherwise repositories are found recursively.

Options:
  --apply          Delete after showing the plan and asking for confirmation
  --yes, -y        With --apply, skip the confirmation prompt
  --include-merged Also delete local branches already merged into the remote's
                   default branch whose remote branch still exists (the remote
                   branch is kept)
  --no-fetch       Use existing remote-tracking refs (read-only; not with --apply)
  --depth N        Directory depth to search for repositories (default: 4)
  --stale-days N   Report never-pushed or untracked branches older than N days (default: 90)
  --jobs N         Repositories to fetch in parallel (default: 6)
  --timeout N      Seconds allowed per remote fetch (default: 60)
  -h, --help       Show this help
EOF
}

APPLY=false
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
    --apply) APPLY=true; shift ;;
    --yes|-y) AUTO_YES=true; shift ;;
    --include-merged) SB_INCLUDE_MERGED=true; shift ;;
    --no-fetch) FETCH=false; shift ;;
    --depth|--stale-days|--jobs|--timeout)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 1; }
      need_number "$1" "$2"
      # shellcheck disable=SC2034  # SB_* settings are read by lib/stale-branches.sh
      case "$1" in
        --depth) DEPTH=$2 ;;
        --stale-days) SB_STALE_DAYS=$2 ;;
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

if $APPLY && ! $FETCH; then
  echo "--apply requires fresh remote data; remove --no-fetch" >&2
  exit 1
fi
if $AUTO_YES && ! $APPLY; then
  echo "--yes only applies together with --apply" >&2
  exit 1
fi
TARGET=${TARGET:-$PWD}
[[ -d "$TARGET" ]] || { echo "Not a directory: $TARGET" >&2; exit 1; }
TARGET=$(cd "$TARGET" && pwd -P) || exit 1

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; CYAN=""; RESET=""
fi

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
  echo "No Git repositories found in $(sb_short_path "$TARGET")."
  exit 0
fi

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
  [[ -t 2 ]] && printf '%sFetching remotes for %d repositor%s…%s\n' "$DIM" "${#REPOS[@]}" \
    "$( [[ ${#REPOS[@]} -eq 1 ]] && echo y || echo ies)" "$RESET" >&2
  running=0
  for i in "${!REPOS[@]}"; do
    [[ "${STATUS[$i]}" == ok ]] || continue
    ( SB_GIT_DIR=${REPOS[$i]}; sb_fetch "$TMP/$i.requests" > "$TMP/$i.failed" 2> "$TMP/$i.errors" ) &
    running=$((running + 1))
    if [[ "$running" -ge "$JOBS" ]]; then wait; running=0; fi
  done
  wait
fi

for i in "${!REPOS[@]}"; do
  [[ "${STATUS[$i]}" == ok ]] || continue
  SB_GIT_DIR=${REPOS[$i]}
  SB_TMP=$TMP/scratch
  sb_classify "$TMP/$i.snapshot" "$TMP/$i.failed" "$TMP/$i.requests" > "$TMP/$i.plan"
  N_CHECKED=$((N_CHECKED + $(sb_git for-each-ref --format=x refs/heads 2>/dev/null | wc -l)))
done

# ── Report ────────────────────────────────────────────────────────────────────

N_DELETE=0; N_DELETE_WT=0; N_BLOCKED=0; N_REVIEW=0; N_UNVERIFIED=0; N_HINT=0
N_CLEAN=0; N_FETCH_FAILED=0; NO_REMOTE=()

print_kind() {
  local file=$1 kind=$2 icon=$3 k name sha detail wt
  while IFS="$SB_SEP" read -r k name sha detail wt; do
    [[ "$k" == "$kind" ]] || continue
    if [[ "$kind" == hint && "$name" == /* ]]; then
      printf '  %s %s\n     %s%s%s\n' "$icon" "$(sb_short_path "$name")" "$DIM" "$detail" "$RESET"
      continue
    fi
    printf '  %s %-32s %s%s%s  %s\n' "$icon" "$name" "$DIM" "${sha:0:7}" "$RESET" "$detail"
    [[ -n "$wt" ]] && printf '     %s└ worktree %s%s\n' "$DIM" "$(sb_short_path "$wt")" "$RESET"
  done < "$file"
}

count_kind() { awk -F "$SB_SEP" -v k="$2" '$1 == k { n++ } END { print n + 0 }' "$1"; }

mode="dry run"
$APPLY && mode="apply"
$FETCH || mode="dry run, no fetch"
echo ""
echo "${BOLD}╔══════════════════════════════════════════╗${RESET}"
printf '%s║  prune_branches: %-24s║%s\n' "$BOLD" "$mode" "$RESET"
echo "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo "  ${DIM}$(sb_short_path "$TARGET") │ ${#REPOS[@]} repositor$( [[ ${#REPOS[@]} -eq 1 ]] && echo y || echo ies)${RESET}"

for i in "${!REPOS[@]}"; do
  name=$(repo_name "${REPOS[$i]}")
  if [[ "${STATUS[$i]}" != ok ]]; then
    NO_REMOTE+=("$name")
    continue
  fi
  plan=$TMP/$i.plan
  failed=""
  [[ -s "$TMP/$i.failed" ]] && failed=$(tr '\n' ' ' < "$TMP/$i.failed")
  if [[ ! -s "$plan" && -z "$failed" ]]; then
    N_CLEAN=$((N_CLEAN + 1))
    continue
  fi
  echo ""
  echo "${BOLD}── ${name} ──────────────────────────────────${RESET}"
  if [[ -n "$failed" ]]; then
    N_FETCH_FAILED=$((N_FETCH_FAILED + 1))
    echo "  ${RED}✘${RESET} Fetch failed for: ${failed% } — branches tracking it were left alone"
    [[ -s "$TMP/$i.errors" ]] && sed -n '1,2s/^/     /p' "$TMP/$i.errors"
  fi
  print_kind "$plan" delete "${RED}✂${RESET} "
  print_kind "$plan" blocked "🟠"
  print_kind "$plan" review "🟡"
  print_kind "$plan" unverified "⚪"
  print_kind "$plan" hint "${CYAN}ℹ${RESET} "
  N_DELETE=$((N_DELETE + $(count_kind "$plan" delete)))
  N_DELETE_WT=$((N_DELETE_WT + $(awk -F "$SB_SEP" '$1 == "delete" && $5 != "" { n++ } END { print n + 0 }' "$plan")))
  N_BLOCKED=$((N_BLOCKED + $(count_kind "$plan" blocked)))
  N_REVIEW=$((N_REVIEW + $(count_kind "$plan" review)))
  N_UNVERIFIED=$((N_UNVERIFIED + $(count_kind "$plan" unverified)))
  N_HINT=$((N_HINT + $(count_kind "$plan" hint)))
done

summary_row() { printf '  %s %-22s %s\n' "$1" "$2" "$3"; }

echo ""
echo "${BOLD}── Summary ──────────────────────────────────${RESET}"
N_WITH_REMOTE=$(( ${#REPOS[@]} - ${#NO_REMOTE[@]} ))
summary_row "🔍" "Checked" "$N_CHECKED local branch(es) in $N_WITH_REMOTE repositor$( [[ "$N_WITH_REMOTE" -eq 1 ]] && echo y || echo ies) with a remote"
if [[ "$N_DELETE" -gt 0 ]]; then
  detail="$N_DELETE branch(es)"
  [[ "$N_DELETE_WT" -gt 0 ]] && detail+=", $N_DELETE_WT with a worktree"
  summary_row "${RED}✂${RESET} " "To delete" "$detail"
fi
[[ "$N_BLOCKED" -gt 0 ]] && summary_row "🟠" "Blocked by worktree" "$N_BLOCKED (uncommitted changes, ignored local files, locked, or checked out)"
[[ "$N_REVIEW" -gt 0 ]] && summary_row "🟡" "Needs review" "$N_REVIEW (upstream gone, but commits not confirmed on a remote)"
[[ "$N_UNVERIFIED" -gt 0 ]] && summary_row "⚪" "Unverified" "$N_UNVERIFIED (remote could not be fetched)"
[[ "$N_HINT" -gt 0 ]] && summary_row "${CYAN}ℹ${RESET} " "Kept, looks stale" "$N_HINT (merged or old, but not prunable; see above)"
[[ "$N_CLEAN" -gt 0 ]] && summary_row "✅" "Nothing to report" "$N_CLEAN repositor$( [[ "$N_CLEAN" -eq 1 ]] && echo y || echo ies)"
if [[ ${#NO_REMOTE[@]} -gt 0 ]]; then
  list="${NO_REMOTE[0]}"
  for ((j = 1; j < ${#NO_REMOTE[@]} && j < 5; j++)); do list+=", ${NO_REMOTE[$j]}"; done
  [[ ${#NO_REMOTE[@]} -gt 5 ]] && list+=" (+$((${#NO_REMOTE[@]} - 5)) more)"
  summary_row "– " "No remote (skipped)" "${#NO_REMOTE[@]}: $list"
fi
[[ "$N_FETCH_FAILED" -gt 0 ]] && summary_row "${RED}✘${RESET} " "Fetch failed" "$N_FETCH_FAILED repositor$( [[ "$N_FETCH_FAILED" -eq 1 ]] && echo y || echo ies)"
$FETCH || echo "  ${DIM}Based on remote-tracking refs from the last fetch.${RESET}"

STATUS_CODE=0
[[ "$N_FETCH_FAILED" -gt 0 ]] && STATUS_CODE=1

if [[ "$N_DELETE" -eq 0 ]]; then
  echo ""
  if [[ "$SB_INCLUDE_MERGED" == true ]]; then
    echo "Nothing to delete."
  else
    echo "Nothing to delete. ${DIM}Only branches whose remote branch was deleted are pruned, unless --include-merged is given.${RESET}"
  fi
  exit "$STATUS_CODE"
fi
if ! $APPLY; then
  echo ""
  apply_cmd="prune_branches --apply"
  [[ "$SB_INCLUDE_MERGED" == true ]] && apply_cmd+=" --include-merged"
  echo "Dry run — nothing was changed. Run ${BOLD}${apply_cmd}${RESET} to delete."
  exit "$STATUS_CODE"
fi

# ── Apply ─────────────────────────────────────────────────────────────────────

echo ""
confirm_apply "Delete $N_DELETE branch(es) and remove $N_DELETE_WT worktree(s)?" "$AUTO_YES" false || exit "$STATUS_CODE"

DELETED=0; REMOVED_WT=0; FAILED=0
for i in "${!REPOS[@]}"; do
  [[ "${STATUS[$i]}" == ok && -s "$TMP/$i.plan" ]] || continue
  SB_GIT_DIR=${REPOS[$i]}
  # shellcheck disable=SC2034  # read by lib/stale-branches.sh
  SB_TMP=$TMP/scratch
  name=$(repo_name "${REPOS[$i]}")
  while IFS="$SB_SEP" read -r kind branch sha detail wt; do
    [[ "$kind" == delete ]] || continue
    if error=$(sb_apply_delete "$branch" "$sha" "$wt" "$detail" </dev/null); then
      DELETED=$((DELETED + 1))
      [[ -n "$wt" ]] && REMOVED_WT=$((REMOVED_WT + 1))
      printf '  %s✔%s %s: %s %s(%s)%s\n' "$GREEN" "$RESET" "$name" "$branch" "$DIM" "${sha:0:12}" "$RESET"
    else
      FAILED=$((FAILED + 1))
      printf '  %s✘%s %s: %s — %s\n' "$RED" "$RESET" "$name" "$branch" "$(printf '%s' "$error" | head -1)"
    fi
  done < "$TMP/$i.plan"
done

echo ""
echo "Deleted $DELETED branch(es), removed $REMOVED_WT worktree(s), $FAILED failed."
if [[ "$DELETED" -gt 0 ]]; then
  echo "${DIM}Restore a branch with: git branch <name> <commit> (each repository logs them in .git/prune-branches.log)${RESET}"
fi
[[ "$FAILED" -gt 0 ]] && STATUS_CODE=1
exit "$STATUS_CODE"
