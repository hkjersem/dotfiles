#!/usr/bin/env bash
#
# Shared stale-branch and worktree classification for prune-branches.sh and
# audit-repo.sh. Bash 3.2 compatible; requires clean-targets.sh.
#
# Callers set SB_GIT_DIR (absolute common Git directory) and SB_TMP (scratch
# directory), then call sb_classify. Records are written to stdout, one per
# line, with fields separated by $SB_SEP:
#   kind  branch-or-path  commit  detail  worktree
# Kinds: delete, delete-worktree, review, blocked, unverified, hint, recent and unpushed-detached (counted, not listed).

SB_SEP=$'\x1f'
SB_PROTECTED="main master develop development trunk"
SB_STALE_SECONDS=$((90 * 86400))
SB_INCLUDE_MERGED="${SB_INCLUDE_MERGED:-false}"
SB_INCLUDE_DETACHED="${SB_INCLUDE_DETACHED:-false}"
SB_OLDER_THAN="${SB_OLDER_THAN:-}"
SB_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SB_FETCH_TIMEOUT="${SB_FETCH_TIMEOUT:-60}"
SB_PATCH_ID_LIMIT=1000
SB_INVOKING_TOP=""

sb_git() { GIT_OPTIONAL_LOCKS=0 git --git-dir="$SB_GIT_DIR" "$@"; }

sb_short_path() {
  # shellcheck disable=SC2088  # abbreviates the path for display
  case "$1" in
    "$HOME") printf '~\n' ;;
    "$HOME"/*) printf '~/%s\n' "${1#"$HOME"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

sb_record() {
  printf '%s%s%s%s%s%s%s%s%s\n' "$1" "$SB_SEP" "$2" "$SB_SEP" "$3" "$SB_SEP" "$4" "$SB_SEP" "${5:-}"
}

# Prints the canonical common Git directory for a path inside a repository or worktree.
sb_common_dir() {
  local dir
  dir=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  (cd "$dir" 2>/dev/null && pwd -P)
}

sb_has_remote() { [[ -n "$(sb_git remote 2>/dev/null)" ]]; }

SB_SNAPSHOT_FILE=prune-branches-upstreams
SB_REMOTES_FILE=prune-branches-remotes

# Prints the URL a remote was last fetched from by this tool, else its configured URL.
sb_fetched_url() {
  local file="$SB_GIT_DIR/$SB_REMOTES_FILE" url=""
  [[ -f "$file" ]] && url=$(awk -F "$SB_SEP" -v r="$1" '$1 == r { u = $2 } END { print u }' "$file")
  [[ -n "$url" ]] || url=$(sb_git config --get "remote.$1.url" 2>/dev/null)
  printf '%s\n' "$url"
}

# Records each remote upstream's commit before fetching, because pruning
# deletes the remote-tracking ref together with its reflog. The record only
# explains review entries. Entries are tied to the remote's URL, so they say
# nothing after the remote is repointed. Entries from earlier runs are kept
# while the local branch exists, so a dry run that prunes does not erase them.
sb_snapshot() {
  local branch upstream remote oid url file="$SB_GIT_DIR/$SB_SNAPSHOT_FILE" branches
  branches=$(sb_git for-each-ref --format='%(refname:lstrip=2)' refs/heads)
  {
    sb_git for-each-ref --format='%(refname:lstrip=2)%1f%(upstream)%1f%(upstream:remotename)' refs/heads |
      while IFS="$SB_SEP" read -r branch upstream remote; do
        [[ "$upstream" == refs/remotes/* && -n "$remote" ]] || continue
        oid=$(sb_git rev-parse -q --verify "$upstream^{commit}" 2>/dev/null) || continue
        url=$(sb_fetched_url "$remote")
        [[ -n "$url" ]] || continue
        printf '%s%s%s%s%s%s%s\n' "$branch" "$SB_SEP" "$oid" "$SB_SEP" "$remote" "$SB_SEP" "$url"
      done
    [[ -f "$file" ]] && while IFS="$SB_SEP" read -r branch oid remote url; do
      [[ -n "$url" ]] || continue
      printf '%s\n' "$branches" | grep -qxF "$branch" \
        && printf '%s%s%s%s%s%s%s\n' "$branch" "$SB_SEP" "$oid" "$SB_SEP" "$remote" "$SB_SEP" "$url"
    done < "$file"
  } | awk '!seen[$0]++'
}

# Saves a snapshot inside the Git directory for later runs.
sb_save_snapshot() {
  cp "$1" "$SB_GIT_DIR/$SB_SNAPSHOT_FILE.tmp" 2>/dev/null \
    && mv "$SB_GIT_DIR/$SB_SNAPSHOT_FILE.tmp" "$SB_GIT_DIR/$SB_SNAPSHOT_FILE"
}

sb_with_timeout() {
  perl -e 'alarm shift @ARGV; exec @ARGV or exit 127' "$SB_FETCH_TIMEOUT" "$@"
}

# Fetches, with pruning, every configured remote that a local branch tracks,
# and lists the refs each remote advertises right after a successful fetch
# into $1, which is the only proof that a commit is still on that remote.
# Prints failed remote names; Git errors go to stderr.
sb_fetch() {
  local remote remotes status url heads advertised=$1 remotes_file="$SB_GIT_DIR/$SB_REMOTES_FILE"
  : > "$advertised"
  remotes=$(sb_git for-each-ref --format='%(upstream:remotename)' refs/heads | sort -u)
  [[ "$SB_INCLUDE_DETACHED" != true ]] || remotes=$(sb_git remote)
  for remote in $remotes; do
    url=$(sb_git config --get "remote.$remote.url" 2>/dev/null) || continue
    status=0
    GIT_TERMINAL_PROMPT=0 sb_with_timeout git --git-dir="$SB_GIT_DIR" -c fetch.recurseSubmodules=false \
      fetch --prune --quiet "$remote" </dev/null || status=$?
    if [[ "$status" -ne 0 ]]; then
      [[ "$status" -eq 142 ]] && echo "git fetch $remote: timed out after ${SB_FETCH_TIMEOUT}s" >&2
      printf '%s\n' "$remote"
      continue
    fi
    { [[ -f "$remotes_file" ]] && awk -F "$SB_SEP" -v r="$remote" '$1 != r' "$remotes_file"
      printf '%s%s%s\n' "$remote" "$SB_SEP" "$url"; } > "$remotes_file.tmp" 2>/dev/null \
      && mv "$remotes_file.tmp" "$remotes_file"
    if ! heads=$(GIT_TERMINAL_PROMPT=0 sb_with_timeout git --git-dir="$SB_GIT_DIR" ls-remote "$remote" </dev/null); then
      printf '%s\n' "$remote"
      continue
    fi
    awk -v r="$remote" -v s="$SB_SEP" \
        '$2 ~ /^refs\/heads\// || $2 ~ /^refs\/(pull|merge-requests)\/[^\/]+\/head$/ { print r s $1 s $2 }' \
      <<< "$heads" >> "$advertised"
  done
}

# Prints the remote-tracking ref for a remote's default branch.
sb_default_ref() {
  local ref
  ref=$(sb_git symbolic-ref -q "refs/remotes/$1/HEAD" 2>/dev/null) && { printf '%s\n' "$ref"; return 0; }
  for ref in "refs/remotes/$1/main" "refs/remotes/$1/master"; do
    sb_git rev-parse -q --verify "$ref" >/dev/null 2>&1 && { printf '%s\n' "$ref"; return 0; }
  done
  return 1
}

sb_is_protected() {
  local name
  for name in $SB_PROTECTED $SB_DEFAULT_NAMES; do
    [[ "$1" == "$name" ]] && return 0
  done
  return 1
}

sb_load_worktrees() {
  SB_WT_PATH=(); SB_WT_BRANCH=(); SB_WT_HEAD=(); SB_WT_FLAGS=()
  local field path="" branch="" head="" flags=""
  while IFS= read -r -d '' field; do
    case "$field" in
      "worktree "*) path=${field#worktree }; branch=""; head=""; flags="" ;;
      "HEAD "*) head=${field#HEAD } ;;
      "branch refs/heads/"*) branch=${field#branch refs/heads/} ;;
      detached) flags+=" detached" ;;
      bare) flags+=" bare" ;;
      locked|"locked "*) flags+=" locked" ;;
      prunable|"prunable "*) flags+=" prunable" ;;
      "")
        [[ -n "$path" ]] || continue
        SB_WT_PATH+=("$path"); SB_WT_BRANCH+=("$branch"); SB_WT_HEAD+=("$head"); SB_WT_FLAGS+=("$flags ")
        path=""
        ;;
    esac
  done < <(sb_git worktree list --porcelain -z 2>/dev/null)
  sb_load_busy
}

# A rebase or bisect detaches HEAD, yet still needs its branch to finish.
sb_load_busy() {
  SB_BUSY_BRANCH=(); SB_BUSY_WHERE=()
  local admin where file name
  for admin in "$SB_GIT_DIR" "$SB_GIT_DIR"/worktrees/*; do
    [[ -d "$admin" ]] || continue
    where=${SB_WT_PATH[0]:-$admin}
    if [[ "$admin" != "$SB_GIT_DIR" ]]; then
      where=$admin
      [[ -f "$admin/gitdir" ]] && { where=$(<"$admin/gitdir"); where=${where%/.git}; }
    fi
    for file in rebase-merge/head-name rebase-apply/head-name BISECT_START; do
      [[ -f "$admin/$file" ]] || continue
      name=$(<"$admin/$file")
      name=${name#refs/heads/}
      [[ -n "$name" ]] || continue
      SB_BUSY_BRANCH+=("$name")
      case "$file" in BISECT*) SB_BUSY_WHERE+=("bisect in progress in $where") ;;
        *) SB_BUSY_WHERE+=("rebase in progress in $where") ;; esac
    done
  done
}

# Prints why a branch is in use by an unfinished rebase or bisect.
sb_busy_for_branch() {
  local i
  for ((i = 0; i < ${#SB_BUSY_BRANCH[@]}; i++)); do
    [[ "${SB_BUSY_BRANCH[$i]}" == "$1" ]] && { printf '%s\n' "${SB_BUSY_WHERE[$i]}"; return 0; }
  done
  return 1
}

# Prints the index of the worktree that has a branch checked out.
sb_worktree_for_branch() {
  local i
  for ((i = 0; i < ${#SB_WT_PATH[@]}; i++)); do
    [[ "${SB_WT_BRANCH[$i]}" == "$1" ]] && { printf '%s\n' "$i"; return 0; }
  done
  return 1
}

# Generated artifacts may be deleted with a worktree; other ignored files may be
# local secrets. A directory counts only when it is itself the ignored entry, so
# an ignored file inside a tracked build/ or dist/ folder is still protected.
sb_regenerable() {
  local path=$1 target base
  if [[ "$path" == */ ]]; then
    base=${path%/}; base=${base##*/}
    for target in "${CLEAN_DIR_TARGETS[@]}"; do
      [[ "$base" == "$target" ]] && return 0
    done
    return 1
  fi
  base=${path##*/}
  [[ "$base" == .DS_Store ]] && return 0
  for target in "${CLEAN_FILE_TARGETS[@]}"; do
    # shellcheck disable=SC2053
    [[ "$base" == $target ]] && return 0
  done
  return 1
}

# Prints why removing a worktree would lose history: refs kept only in that
# worktree, or commits in its HEAD reflog that no ref and no earlier state of
# the branch ($2) reaches, such as commits made on a detached HEAD.
sb_worktree_history() {
  local refs wt_oids branch_oids lost detail=""
  refs=$(GIT_OPTIONAL_LOCKS=0 git -C "$1" for-each-ref --format='%(refname)' \
    refs/worktree/ refs/bisect/ refs/rewritten/ 2>/dev/null) \
    || { printf 'worktree refs could not be read\n'; return 0; }
  refs=$(printf '%s\n' "$refs" | grep -c .)
  [[ "$refs" -gt 0 ]] && detail="$refs ref(s) only in this worktree"
  wt_oids=$(GIT_OPTIONAL_LOCKS=0 git -C "$1" reflog show --format=%H HEAD 2>/dev/null) \
    || { printf 'HEAD history could not be read\n'; return 0; }
  wt_oids=$(printf '%s\n' "$wt_oids" | sort -u)
  branch_oids=""
  if [[ -n "$2" ]]; then
    branch_oids=$(sb_git reflog show --format=%H "refs/heads/$2" 2>/dev/null) \
      || { printf 'branch history could not be read\n'; return 0; }
    branch_oids=$(printf '%s\n' "$branch_oids" | sort -u)
  fi
  if [[ -n "$wt_oids" ]]; then
    # Object IDs never contain whitespace, so word splitting is safe here.
    # shellcheck disable=SC2086,SC2048
    lost=$(sb_git rev-list --count $wt_oids --not --all ${SB_WT_HEAD[*]} $branch_oids 2>/dev/null) \
      || lost="?"
    if [[ "$lost" != 0 ]]; then
      [[ -n "$detail" ]] && detail+=", "
      detail+="$lost commit(s) only in its HEAD history"
    fi
  fi
  [[ -n "$detail" ]] && printf '%s\n' "$detail"
  return 0
}

# Prints why a worktree is unsafe to remove, or nothing when it is clean.
sb_worktree_dirt() {
  local entry code file changes=0 ignored=() skip=false detail="" history
  if ! GIT_OPTIONAL_LOCKS=0 git -C "$1" status --porcelain=v1 -z --untracked-files=normal \
      --ignored=matching > "$SB_TMP/status" 2>/dev/null; then
    printf 'status could not be read\n'
    return 0
  fi
  while IFS= read -r -d '' entry; do
    if $skip; then skip=false; continue; fi
    code=${entry:0:2}
    file=${entry:3}
    case "$code" in R*|C*) skip=true ;; esac
    if [[ "$code" == '!!' ]]; then
      sb_regenerable "$file" || ignored+=("$file")
    else
      changes=$((changes + 1))
    fi
  done < "$SB_TMP/status"
  # Status hides edits to files flagged assume-unchanged (lowercase tag) or
  # skip-worktree (S); sparse-checkout entries without a file on disk are fine.
  local hidden=0 tag
  if ! git -C "$1" ls-files -v -z > "$SB_TMP/status" 2>/dev/null; then
    printf 'index could not be read\n'
    return 0
  fi
  while IFS= read -r -d '' entry; do
    tag=${entry:0:1}
    file=${entry:2}
    case "$tag" in
      [[:lower:]]) hidden=$((hidden + 1)) ;;
      S) [[ -e "$1/$file" || -L "$1/$file" ]] && hidden=$((hidden + 1)) ;;
    esac
  done < "$SB_TMP/status"
  [[ "$changes" -gt 0 ]] && detail="$changes uncommitted change(s)"
  if [[ "$hidden" -gt 0 ]]; then
    [[ -n "$detail" ]] && detail+=", "
    detail+="$hidden file(s) marked assume-unchanged or skip-worktree"
  fi
  if [[ ${#ignored[@]} -gt 0 ]]; then
    [[ -n "$detail" ]] && detail+=", "
    detail+="ignored local files: ${ignored[0]}"
    [[ ${#ignored[@]} -gt 1 ]] && detail+=", ${ignored[1]}"
    [[ ${#ignored[@]} -gt 2 ]] && detail+=" (+$((${#ignored[@]} - 2)) more)"
  fi
  if [[ $# -ge 2 ]]; then
    history=$(sb_worktree_history "$1" "$2")
    if [[ -n "$history" ]]; then
      [[ -n "$detail" ]] && detail+=", "
      detail+=$history
    fi
  fi
  [[ -n "$detail" ]] && printf '%s\n' "$detail"
  return 0
}

# Succeeds when merging $2 into $1 would change nothing, so all of the branch's
# content is already there. New objects go to a scratch directory, keeping the
# repository unchanged.
sb_merge_is_noop() {
  local out base_tree status=0 objects="$SB_TMP/merge-objects"
  base_tree=$(sb_git rev-parse -q --verify "$1^{tree}" 2>/dev/null) || return 1
  rm -rf "$objects" && mkdir -p "$objects" || return 1
  out=$(GIT_OBJECT_DIRECTORY="$objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$SB_GIT_DIR/objects" \
    sb_git merge-tree --write-tree "$1" "$2" 2>/dev/null) || status=1
  rm -rf "$objects"
  [[ "$status" -eq 0 && "${out%%$'\n'*}" == "$base_tree" ]]
}

# --verbatim keeps whitespace, --binary keeps binary content and short submodule
# output keeps gitlink changes; patch-id would otherwise ignore all three.
# Prints "<patch-id> <commit>" lines.
sb_patch_ids() {
  sb_git -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.relative=false "$@" \
    -U3 --inter-hunk-context=0 --no-ext-diff --no-textconv --no-color --binary --full-index --submodule=short \
    --ignore-submodules=none 2>/dev/null | git patch-id --verbatim 2>/dev/null
}

# Succeeds when a snapshot shows the branch's commits were pushed to its remote
# branch before that was deleted. This explains a review entry but never
# justifies deletion: the deleted remote branch may have been the only copy.
sb_was_pushed() {
  local branch=$1 sha=$2 remote=$3 snapshot_file=$4 snapshot url
  [[ -n "$snapshot_file" && -f "$snapshot_file" ]] || return 1
  url=$(sb_git config --get "remote.$remote.url" 2>/dev/null)
  for snapshot in $(awk -F "$SB_SEP" -v b="$branch" -v r="$remote" -v u="$url" \
      '$1 == b && $3 == r && $4 == u { print $2 }' "$snapshot_file"); do
    sb_git merge-base --is-ancestor "$sha" "$snapshot" 2>/dev/null && return 0
  done
  return 1
}

# Prints a containing remote branch, requiring advertised ref/commit equality
# when fresh fetch results are supplied. An empty remote searches all remotes.
sb_containing_remote() {
  local sha=$1 remote=$2 advertised=$3 default="" ref pattern=refs/remotes/
  if [[ -n "$remote" ]]; then
    default=$(sb_default_ref "$remote" 2>/dev/null) || default=""
    pattern+="$remote/"
  fi
  if [[ -n "$advertised" ]]; then
    [[ -f "$advertised" ]] || return 1
    ref=$(sb_git for-each-ref --contains "$sha" --format="%(refname)$SB_SEP%(objectname)" refs/remotes/ 2>/dev/null \
      | awk -F "$SB_SEP" -v s="$SB_SEP" -v d="$default" '
          NR == FNR { if ($3 ~ /^refs\/heads\//) ok["refs/remotes/" $1 "/" substr($3, 12) s $2] = 1; next }
          ($1 s $2) in ok { if ($1 == d) { print substr($1, 14); found = 1; exit } if (first == "") first = substr($1, 14) }
          END { if (!found && first != "") print first }' "$advertised" -) || return 1
  else
    ref=$(sb_git for-each-ref --contains "$sha" --format='%(refname:lstrip=2)' "$pattern" 2>/dev/null \
      | grep -v '/HEAD$' | awk -v d="${default#refs/remotes/}" '
          $0 == d { print; found = 1; exit } first == "" { first = $0 } END { if (!found && first != "") print first }') || return 1
  fi
  [[ -n "$ref" ]] || return 1
  printf '%s\n' "$ref"
}

# Prints a retention reason; no output means the optional age guard passed.
sb_age_reason() {
  [[ -n "$SB_OLDER_THAN" ]] || return 0
  local stamp detail args
  stamp=$(sb_git show -s --format=%ct "$1" 2>/dev/null) \
    || { printf 'commit age could not be read\n'; return 0; }
  args=(--older-than "$SB_OLDER_THAN" --commit-time "$stamp")
  [[ -z "${2:-}" ]] || args+=(--path "$2")
  if detail=$(python3 -B "$SB_LIB_DIR/../prune-age.py" "${args[@]}" 2>&1); then
    return 0
  fi
  printf '%s\n' "${detail:-age could not be verified}"
}

# Detached worktrees must also retain HEAD history and unfinished operations.
sb_detached_reason() {
  local i=$1 path admin state dirt
  path=${SB_WT_PATH[$i]}
  if [[ "$i" -eq 0 || "$path" == "$SB_INVOKING_TOP" ]]; then
    printf 'main or invoking worktree\n'; return 0
  fi
  case "${SB_WT_FLAGS[$i]}" in
    *" locked "*) printf 'worktree is locked\n'; return 0 ;;
    *" prunable "*) printf 'worktree directory is missing\n'; return 0 ;;
  esac
  [[ -d "$path" && ! -L "$path" ]] || { printf 'not a real worktree directory\n'; return 0; }
  admin=$(GIT_OPTIONAL_LOCKS=0 git -C "$path" rev-parse --absolute-git-dir 2>/dev/null) \
    || { printf 'worktree Git directory could not be read\n'; return 0; }
  for state in rebase-merge rebase-apply BISECT_LOG MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD sequencer; do
    [[ ! -e "$admin/$state" ]] || { printf 'unfinished Git operation: %s\n' "$state"; return 0; }
  done
  dirt=$(sb_worktree_dirt "$path" "")
  [[ -z "$dirt" ]] || { printf '%s\n' "$dirt"; return 0; }
  sb_age_reason "${SB_WT_HEAD[$i]}" "$path"
}

# Prints evidence that a branch's commits still exist on a remote; fails when none is found.
sb_safe_evidence() {
  local sha=$1 remote=$2 advertised=$3 ref base mb pid commits commit newest line
  if [[ -n "$advertised" && -f "$advertised" ]]; then
    ref=$(awk -F "$SB_SEP" -v r="$remote" -v s="$sha" \
      '$1 == r && $2 == s && $3 !~ /^refs\/heads\// { print $3; exit }' "$advertised")
    if [[ -n "$ref" ]]; then
      ref=${ref#refs/}; ref=${ref%/head}
      printf 'pushed as %s on %s\n' "$ref" "$remote"
      return 0
    fi
    # Only remote-tracking refs that match what a remote advertised after this
    # run's fetch count; others may be stale copies of deleted branches.
    ref=$(sb_containing_remote "$sha" "$remote" "$advertised") || ref=""
  else
    # Without a fetch, only the branch's own remote counts, as of the last fetch.
    ref=$(sb_containing_remote "$sha" "$remote" "") || ref=""
  fi
  [[ -n "$ref" ]] && { printf 'already in %s\n' "$ref"; return 0; }
  base=$(sb_default_ref "$remote") || return 1
  mb=$(sb_git merge-base "$base" "$sha" 2>/dev/null) || return 1
  sb_patch_ids log -p --no-merges --max-count="$SB_PATCH_ID_LIMIT" "$mb..$base" > "$SB_TMP/upstream-ids"
  # Patch-ids show which upstream commits carry the branch's changes, but ignore
  # where in a file an edit lands. Merging the branch into the newest matching
  # commit must also change nothing, which later upstream edits cannot affect.
  # Not git cherry: it ignores whitespace and skips merge commits, which can carry unpushed changes.
  commits=$(sb_git rev-list --max-count=201 "$mb..$sha" 2>/dev/null) || return 1
  if [[ -n "$commits" && "$(sb_git rev-list --count --merges "$mb..$sha" 2>/dev/null)" == 0 \
      && $(printf '%s\n' "$commits" | wc -l) -le 200 ]]; then
    newest=""
    for commit in $commits; do
      pid=$(sb_patch_ids show --format= "$commit" | cut -d' ' -f1)
      line=""
      [[ -n "$pid" ]] && line=$(awk -v p="$pid" '$1 == p { print NR; exit }' "$SB_TMP/upstream-ids")
      if [[ -z "$line" ]]; then newest=""; break; fi
      [[ -z "$newest" || "$line" -lt "$newest" ]] && newest=$line
    done
    if [[ -n "$newest" ]] \
        && sb_merge_is_noop "$(awk -v n="$newest" 'NR == n { print $2 }' "$SB_TMP/upstream-ids")" "$sha"; then
      printf 'rebased into %s\n' "${base#refs/remotes/}"
      return 0
    fi
  fi
  pid=$(sb_patch_ids diff "$mb" "$sha" | cut -d' ' -f1)
  [[ -n "$pid" ]] || return 1
  commit=$(awk -v p="$pid" '$1 == p { print $2; exit }' "$SB_TMP/upstream-ids")
  if [[ -n "$commit" ]] && sb_merge_is_noop "$commit" "$sha"; then
    printf 'squash-merged into %s\n' "${base#refs/remotes/}"
    return 0
  fi
  return 1
}

# Classifies local branches and linked worktrees. Arguments: snapshot file,
# failed-remotes file and request-heads file (any may be empty or missing).
sb_classify() {
  local snapshot_file=${1:-} failed_file=${2:-} request_file=${3:-} now remote ref i detail
  local branch sha upstream track remotename remoteref date evidence wt wt_path dirt unique default_ref busy
  local state ahead behind local_tracking default_remote
  now=$(date +%s)
  sb_load_worktrees
  SB_DEFAULT_NAMES=""
  for remote in $(sb_git remote 2>/dev/null); do
    ref=$(sb_default_ref "$remote") && SB_DEFAULT_NAMES+=" ${ref#refs/remotes/"$remote"/}"
  done
  default_remote=$(sb_git remote 2>/dev/null | head -1)
  default_ref=$(sb_default_ref "$default_remote") || default_ref=""

  while IFS="$SB_SEP" read -r branch sha upstream track remotename remoteref date; do
    wt=$(sb_worktree_for_branch "$branch") || wt=""
    wt_path=""
    [[ -n "$wt" ]] && wt_path=${SB_WT_PATH[$wt]}

    # A branch tracking another local branch (git checkout -b x main) has no
    # remote branch of its own; it is deleted only when its commits are
    # confirmed on the default remote, and only with --include-merged.
    local_tracking=false
    evidence=""
    if [[ "$upstream" == refs/heads/* ]] && ! sb_is_protected "$branch" && [[ -n "$default_remote" ]] \
        && evidence=$(sb_safe_evidence "$sha" "$default_remote" "$request_file"); then
      if [[ "$SB_INCLUDE_MERGED" != true ]]; then
        sb_record hint "$branch" "$sha" \
          "tracks local ${upstream#refs/heads/}; $evidence (prune with --include-merged)" "$wt_path"
        continue
      fi
      if [[ -n "$failed_file" && -f "$failed_file" ]] && grep -qxF "$default_remote" "$failed_file"; then
        sb_record unverified "$branch" "$sha" "could not fetch $default_remote" "$wt_path"
        continue
      fi
      local_tracking=true
    fi

    if [[ "$local_tracking" != true && ( -z "$upstream" || "$upstream" == refs/heads/* ) ]]; then
      # A branch created without tracking may still exist on a remote under the same name.
      state="never pushed"
      [[ -z "$upstream" ]] || state="tracks local ${upstream#refs/heads/}, not pushed"
      ref=$(sb_git for-each-ref --count=1 --format='%(refname:lstrip=2)' "refs/remotes/*/$branch" 2>/dev/null)
      if [[ -n "$ref" ]]; then
        read -r ahead behind < <(sb_git rev-list --left-right --count "$sha...refs/remotes/$ref" 2>/dev/null)
        state="not tracking $ref (${ahead:-?} ahead, ${behind:-?} behind)"
      fi
      if [[ -n "$date" && $((now - date)) -ge "$SB_STALE_SECONDS" ]]; then
        sb_record hint "$branch" "$sha" "$state, last commit $(( (now - date) / 86400 )) days ago" "$wt_path"
      else
        if [[ -n "$default_ref" ]] && ! sb_is_protected "$branch" \
            && sb_git merge-base --is-ancestor "$sha" "$default_ref" 2>/dev/null; then
          sb_record hint "$branch" "$sha" "$state, no commits beyond ${default_ref#refs/remotes/}" "$wt_path"
        elif ! sb_is_protected "$branch"; then
          sb_record recent "$branch" "$sha" "$state, newer than --stale-after" "$wt_path"
        fi
      fi
      continue
    fi
    if [[ "$local_tracking" == true ]]; then
      evidence="$evidence; tracks local ${upstream#refs/heads/}"
    elif [[ "$upstream" != refs/remotes/* ]]; then
      continue
    elif [[ "$track" != gone ]]; then
      # Merged pull requests whose remote branch was not deleted are only deleted with --include-merged.
      [[ "${remoteref#refs/heads/}" == "$branch" ]] && ! sb_is_protected "$branch" \
        && ref=$(sb_default_ref "$remotename" 2>/dev/null) \
        && sb_git merge-base --is-ancestor "$sha" "$ref" 2>/dev/null || continue
      if [[ "$SB_INCLUDE_MERGED" != true ]]; then
        sb_record hint "$branch" "$sha" \
          "merged into ${ref#refs/remotes/}, but ${upstream#refs/remotes/} still exists (prune with --include-merged)" \
          "$wt_path"
        continue
      fi
      if [[ -n "$failed_file" && -f "$failed_file" ]] && grep -qxF "$remotename" "$failed_file"; then
        sb_record unverified "$branch" "$sha" "could not fetch $remotename" "$wt_path"
        continue
      fi
      # The default ref may be stale (for example outside the fetch refspec), so
      # it must match what the remote advertised during this run's fetch.
      if [[ -n "$request_file" && -f "$request_file" ]] \
          && ! awk -F "$SB_SEP" -v r="$remotename" -v s="$(sb_git rev-parse -q --verify "$ref")" \
            -v h="refs/heads/${ref#refs/remotes/"$remotename"/}" \
            '$1 == r && $2 == s && $3 == h { found = 1 } END { exit !found }' "$request_file"; then
        sb_record unverified "$branch" "$sha" "${ref#refs/remotes/} does not match the remote; fetch it to confirm the merge" "$wt_path"
        continue
      fi
      evidence="merged into ${ref#refs/remotes/}; ${upstream#refs/remotes/} is kept"
    else
      if sb_is_protected "$branch"; then
        sb_record review "$branch" "$sha" "protected branch; upstream ${upstream#refs/remotes/} is gone" "$wt_path"
        continue
      fi
      if ! sb_git config --get "remote.$remotename.url" >/dev/null 2>&1; then
        sb_record review "$branch" "$sha" "remote '$remotename' is no longer configured" "$wt_path"
        continue
      fi
      if [[ -n "$failed_file" && -f "$failed_file" ]] && grep -qxF "$remotename" "$failed_file"; then
        sb_record unverified "$branch" "$sha" "could not fetch $remotename" "$wt_path"
        continue
      fi
      if [[ "${remoteref#refs/heads/}" != "$branch" ]]; then
        sb_record review "$branch" "$sha" "tracked ${upstream#refs/remotes/}, not its own remote branch" "$wt_path"
        continue
      fi

      if ! evidence=$(sb_safe_evidence "$sha" "$remotename" "$request_file"); then
        if sb_was_pushed "$branch" "$sha" "$remotename" "$snapshot_file"; then
          detail="was pushed to $remotename/$branch, but is not merged or in a pull request"
        else
          unique=$(sb_git rev-list --count "$sha" --not --remotes 2>/dev/null) || unique="?"
          detail="upstream gone; $unique commit(s) not found on any remote"
        fi
        if [[ -n "$wt" && "$wt" -ne 0 ]]; then
          dirt=$(sb_worktree_dirt "$wt_path")
          [[ -n "$dirt" ]] && detail+="; worktree has $dirt"
        fi
        sb_record review "$branch" "$sha" "$detail" "$wt_path"
        continue
      fi
    fi

    if busy=$(sb_busy_for_branch "$branch"); then
      sb_record blocked "$branch" "$sha" "$evidence; $busy" "$wt_path"
      continue
    fi
    if [[ -n "$wt" ]]; then
      if [[ "$wt" -eq 0 || "$wt_path" == "$SB_INVOKING_TOP" ]]; then
        sb_record blocked "$branch" "$sha" "$evidence; checked out — switch branch to delete" "$wt_path"
        continue
      fi
      case "${SB_WT_FLAGS[$wt]}" in
        *" locked "*) sb_record blocked "$branch" "$sha" "$evidence; worktree is locked" "$wt_path"; continue ;;
        *" prunable "*)
          sb_record blocked "$branch" "$sha" "$evidence; worktree directory is missing (git worktree prune)" "$wt_path"
          continue ;;
      esac
      dirt=$(sb_worktree_dirt "$wt_path" "$branch")
      if [[ -n "$dirt" ]]; then
        sb_record blocked "$branch" "$sha" "$evidence; worktree has $dirt" "$wt_path"
        continue
      fi
    fi
    detail=$(sb_age_reason "$sha" "$wt_path")
    if [[ -n "$detail" ]]; then
      sb_record blocked "$branch" "$sha" "$detail" "$wt_path"
    else
      sb_record delete "$branch" "$sha" "$evidence" "$wt_path"
    fi
  done < <(sb_git for-each-ref --format='%(refname:lstrip=2)%1f%(objectname)%1f%(upstream)%1f%(upstream:track,nobracket)%1f%(upstream:remotename)%1f%(upstream:remoteref)%1f%(committerdate:unix)' refs/heads 2>/dev/null)

  # Detached worktrees remain report-only unless explicitly included.
  for ((i = 1; i < ${#SB_WT_PATH[@]}; i++)); do
    case "${SB_WT_FLAGS[$i]}" in *" detached "*) ;; *) continue ;; esac
    [[ "${SB_WT_PATH[$i]}" == "$SB_INVOKING_TOP" ]] && continue
    if [[ "${SB_WT_PATH[$i]}" == *$'\n'* || "${SB_WT_PATH[$i]}" == *"$SB_SEP"* ]]; then
      printf -v detail '%q' "${SB_WT_PATH[$i]}"
      sb_record hint "$detail" "${SB_WT_HEAD[$i]}" "kept: path cannot be represented in the pruning plan"
      continue
    fi
    case "${SB_WT_FLAGS[$i]}" in
      *" prunable "*)
        sb_record hint "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached worktree directory is missing (git worktree prune)"
        continue ;;
    esac
    if [[ "$SB_INCLUDE_DETACHED" == true ]]; then
      ref=$(sb_containing_remote "${SB_WT_HEAD[$i]}" "" "$request_file") || ref=""
      if [[ -z "$ref" ]]; then
        sb_record unverified "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached HEAD not confirmed on a remote branch" "${SB_WT_PATH[$i]}"
        continue
      fi
      dirt=$(sb_detached_reason "$i")
      if [[ -n "$dirt" ]]; then
        sb_record blocked "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "$dirt" "${SB_WT_PATH[$i]}"
      else
        sb_record delete-worktree "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached HEAD already in $ref" "${SB_WT_PATH[$i]}"
      fi
      continue
    fi
    ref=$(sb_containing_remote "${SB_WT_HEAD[$i]}" "" "") || ref=""
    if [[ -z "$ref" ]]; then
      sb_record unpushed-detached "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached HEAD not on a remote"
      continue
    fi
    dirt=$(sb_worktree_dirt "${SB_WT_PATH[$i]}")
    if [[ -n "$dirt" ]]; then
      sb_record hint "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached worktree, commit already in $ref, but has $dirt"
    else
      sb_record hint "${SB_WT_PATH[$i]}" "${SB_WT_HEAD[$i]}" "detached worktree, clean, commit already in $ref"
    fi
  done
}

# Removes a planned branch (and its clean worktree) only if nothing changed since planning.
sb_apply_delete() {
  local branch=$1 sha=$2 wt_path=${3:-} detail=$4 dirt i found=false busy
  sb_load_worktrees
  if busy=$(sb_busy_for_branch "$branch"); then
    printf '%s\n' "$busy"
    return 1
  fi
  dirt=$(sb_age_reason "$sha" "$wt_path")
  if [[ -n "$dirt" ]]; then printf '%s\n' "$dirt"; return 1; fi
  if [[ -n "$wt_path" ]]; then
    # The worktree must still have the planned branch at the planned commit;
    # a detached or advanced checkout may hold commits nothing else references.
    for ((i = 0; i < ${#SB_WT_PATH[@]}; i++)); do
      [[ "${SB_WT_PATH[$i]}" == "$wt_path" ]] || continue
      [[ "${SB_WT_BRANCH[$i]}" == "$branch" && "${SB_WT_HEAD[$i]}" == "$sha" ]] && found=true
    done
    if ! $found; then
      printf 'worktree no longer has %s at the planned commit\n' "$branch"
      return 1
    fi
    dirt=$(sb_worktree_dirt "$wt_path" "$branch")
    if [[ -n "$dirt" ]]; then
      printf 'worktree changed since planning: %s\n' "$dirt"
      return 1
    fi
    sb_git worktree remove "$wt_path" 2>&1 || return 1
  fi
  sb_load_worktrees
  if busy=$(sb_busy_for_branch "$branch"); then
    printf '%s\n' "$busy"
    return 1
  fi
  if sb_worktree_for_branch "$branch" >/dev/null; then
    printf 'branch is checked out in a worktree\n'
    return 1
  fi
  sb_git update-ref -m "prune-branches: upstream gone" -d "refs/heads/$branch" "$sha" 2>&1 || return 1
  sb_git config --remove-section "branch.$branch" >/dev/null 2>&1
  printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$branch" "$sha" "$detail" \
    >> "$SB_GIT_DIR/prune-branches.log" 2>/dev/null
  return 0
}

sb_apply_detached() {
  local path=$1 sha=$2 detail=$3 advertised=$4 i reason found=false
  sb_load_worktrees
  for ((i = 1; i < ${#SB_WT_PATH[@]}; i++)); do
    [[ "${SB_WT_PATH[$i]}" == "$path" ]] || continue
    [[ "${SB_WT_HEAD[$i]}" == "$sha" && "${SB_WT_FLAGS[$i]}" == *" detached "* ]] || break
    reason=$(sb_detached_reason "$i")
    [[ -z "$reason" ]] || { printf '%s\n' "$reason"; return 1; }
    found=true
    break
  done
  $found || { printf 'worktree no longer detached at planned commit\n'; return 1; }
  [[ -f "$advertised" ]] && sb_containing_remote "$sha" "" "$advertised" >/dev/null \
    || { printf 'detached HEAD no longer confirmed on a remote branch\n'; return 1; }
  sb_git worktree remove "$path" 2>&1 || return 1
  printf '%s\tworktree:%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$path" "$sha" "$detail" \
    >> "$SB_GIT_DIR/prune-branches.log"
}
