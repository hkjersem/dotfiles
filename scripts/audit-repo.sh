#!/usr/bin/env bash
#
# audit-repo.sh — read-only triage audit for git repositories.
# Runs git signals for any repo; adds JS/TS-specific checks when package.json is present.
# Run from inside a project directory or pass --dir.
#
# Usage:
#   audit-repo [options]
#
# Options:
#   --dir PATH       Target directory (default: current dir)
#   --since PERIOD   Git lookback for churn/firefighting (default: "1 year ago")
#   --skip-security  Skip package manager security audit
#   --no-package-manager  Skip package managers and Node execution
#   -h, --help       Show this help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/pm-utils.sh
source "$SCRIPT_DIR/lib/pm-utils.sh" || exit 1
# shellcheck source=lib/clean-targets.sh
source "$SCRIPT_DIR/lib/clean-targets.sh" || exit 1
# shellcheck source=lib/stale-branches.sh
source "$SCRIPT_DIR/lib/stale-branches.sh" || exit 1
# shellcheck source=lib/output.sh
source "$SCRIPT_DIR/lib/output.sh" || exit 1

# ── Argument parsing ──────────────────────────────────────────────────────────

TARGET_DIR="$PWD"
SINCE="1 year ago"
SKIP_AUDIT=false
NO_PACKAGE_MANAGER=false

usage() {
  cat <<EOF
Usage: audit-repo [options]

Read-only triage audit for git repositories.
Runs git signals for any repo; adds JS/TS-specific checks when package.json is present.

Options:
  --dir PATH             Target directory (default: current dir)
  --since PERIOD         Lookback duration (e.g. 7d) or Git date expression (default: "1 year ago")
  --skip-security        Skip package manager security audit
  --no-package-manager   Skip package managers and Node execution; keep local checks
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir=*)    TARGET_DIR="${1#--dir=}"; shift ;;
    --dir)      [[ $# -ge 2 ]] || { echo "Missing value for --dir" >&2; exit 1; }
                TARGET_DIR="$2"; shift 2 ;;
    --since=*)  SINCE="${1#--since=}"; shift ;;
    --since)    [[ $# -ge 2 ]] || { echo "Missing value for --since" >&2; exit 1; }
                SINCE="$2"; shift 2 ;;
    --skip-security) SKIP_AUDIT=true; shift ;;
    --no-package-manager) NO_PACKAGE_MANAGER=true; shift ;;
    --help|-h)  usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if $NO_PACKAGE_MANAGER; then
  export GIT_NO_LAZY_FETCH=1 GIT_OPTIONAL_LOCKS=0
fi

[[ -n "$TARGET_DIR" && -n "$SINCE" ]] || { echo "--dir and --since must not be empty" >&2; exit 1; }
if [[ "$SINCE" =~ ^[0-9]+[mhdw]$ ]]; then
  SINCE_SECONDS=$(parse_age_seconds "$SINCE") || { echo "Error: invalid --since duration." >&2; exit 2; }
  SINCE="$SINCE_SECONDS seconds ago"
fi
if ! cd "$TARGET_DIR"; then
  echo "$ERR_FAIL Could not cd into ${TARGET_DIR}" >&2
  exit 1
fi

# ── Counters + formatting ─────────────────────────────────────────────────────

ISSUES=0
WARNINGS=0

# Signal tracking for one-liner summary at the end
VELOCITY_TREND=""   # accelerating / steady / slowing
BIG_FILES=0         # source files >500 lines

SECTION_OUTPUT=()
SECTION_ISSUES=0
SECTION_WARNINGS=0
SECTION_CHECKS=0
CURRENT_SECTION=""

section_start() {
  CURRENT_SECTION="$1"
  SECTION_OUTPUT=()
  SECTION_ISSUES=0
  SECTION_WARNINGS=0
  SECTION_CHECKS=0
}

section_end() {
  out_section "$CURRENT_SECTION"
  [[ ${#SECTION_OUTPUT[@]} -gt 0 ]] && printf '%s\n' "${SECTION_OUTPUT[@]}"
  [[ $SECTION_CHECKS -gt 0 && $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 ]] \
    && echo "  $OUT_OK All checks passed"
}

ok()   { SECTION_OUTPUT+=("  $OUT_OK $1"); ((SECTION_CHECKS++)); }
warn() { SECTION_OUTPUT+=("  $OUT_WARN $1"); ((WARNINGS++)); ((SECTION_WARNINGS++)); ((SECTION_CHECKS++)); }
fail() { SECTION_OUTPUT+=("  $OUT_FAIL $1"); ((ISSUES++)); ((SECTION_ISSUES++)); ((SECTION_CHECKS++)); }
info() { SECTION_OUTPUT+=("  $OUT_INFO $1"); }
row()  { SECTION_OUTPUT+=("    ${DIM}$1${RESET}"); }

# ── Preflight ─────────────────────────────────────────────────────────────────

if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "$ERR_FAIL Not inside a git repository." >&2
  exit 1
fi

GIT_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$GIT_ROOT" || exit 1
REPO_NAME=$(basename "$GIT_ROOT")
# Hosted CI, other contributors and remote branches all need a remote.
HAS_REMOTE=
[[ -n "$(git remote 2>/dev/null)" ]] && HAS_REMOTE=1

if [[ ! -f "package.json" ]]; then
  HAS_PACKAGE_JSON=false
else
  HAS_PACKAGE_JSON=true
fi

AUDIT_TMP=$(mktemp -d) || exit 1
trap 'rm -f "$AUDIT_TMP/tracked" "$AUDIT_TMP/stderr" "$AUDIT_TMP/sizes" "$AUDIT_TMP/status" "$AUDIT_TMP/upstream-ids" "$AUDIT_TMP/doc-files.z" "$AUDIT_TMP/doc-files" "$AUDIT_TMP/doc-paths" "$AUDIT_TMP/doc-links" "$AUDIT_TMP/roadmaps"; rm -rf "$AUDIT_TMP/merge-objects"; rmdir "$AUDIT_TMP"' EXIT

command_error() {
  local line
  while IFS= read -r line; do row "$line"; done < "$AUDIT_TMP/stderr"
}

read_git() {
  if ! git "$@" 2>"$AUDIT_TMP/stderr"; then
    printf 'Could not read Git data: git %s\n' "$*" >&2
    cat "$AUDIT_TMP/stderr" >&2
    return 1
  fi
}

# ── Package manager detection ─────────────────────────────────────────────────

# shellcheck source=scripts/package-manager/_detect-pm.sh
if $HAS_PACKAGE_JSON; then
  command -v jq &>/dev/null || { echo "jq is required to audit package.json" >&2; exit 1; }
  jq -e 'type == "object" and ((.packageManager // "") | type == "string")' package.json >/dev/null \
    || { echo "Invalid package.json or packageManager field" >&2; exit 1; }
  source "$SCRIPT_DIR/package-manager/_detect-pm.sh" || exit 1
  PKG_MGR_FIELD=$(jq -r '.packageManager // empty' package.json | cut -d@ -f1)
  if [[ -n "$PKG_MGR_FIELD" || -f bun.lock || -f bun.lockb || -f pnpm-lock.yaml || -f pnpm-workspace.yaml || -f package-lock.json ]]; then
    PM=$(_detect_pm) || exit 1
  elif [[ -f yarn.lock ]]; then
    PM="yarn"
  else
    PM="npm"
  fi
else
  PM="n/a"
fi

# Lockfile detection — done early so security section can use it
LOCKFILES=()
[[ -f "package-lock.json" ]]               && LOCKFILES+=("package-lock.json")
[[ -f "pnpm-lock.yaml" ]]                  && LOCKFILES+=("pnpm-lock.yaml")
[[ -f "yarn.lock" ]]                       && LOCKFILES+=("yarn.lock")
[[ -f "bun.lock" || -f "bun.lockb" ]]      && LOCKFILES+=("bun.lock")

# Workspace detection — check common workspace formats across ecosystems
WORKSPACE_INFO=""
if $HAS_PACKAGE_JSON; then
  # pnpm-workspace.yaml may hold only settings (cooldowns, overrides); it is a
  # workspace only when it lists packages. Otherwise check package.json "workspaces".
  if [[ -f "pnpm-workspace.yaml" ]] && awk '
       function strip(t) { sub(/(^|[ \t])#.*$/, "", t); return t }
       function flow(t,   i) {
         if ((i = index(t, "]"))) { if (substr(t, 1, i - 1) ~ /[^ \t,]/) found = 1; done = 1 }
         else if (t ~ /[^ \t,]/) { found = 1; done = 1 }
       }
       { sub(/\r$/, ""); line = strip($0) }
       in_flow { flow(line); if (done) exit; next }
       in_list && line ~ /^[ \t]*-([ \t]|$)/ { found = 1; exit }
       in_list && line ~ /^[^ \t]/ { exit }
       !in_list && line ~ /^["\047]?packages["\047]?[ \t]*:/ {
         sub(/^["\047]?packages["\047]?[ \t]*:[ \t]*/, "", line)
         if (line ~ /^\[/) { in_flow = 1; flow(substr(line, 2)); if (done) exit }
         else if (line ~ /[^ \t]/) exit
         else in_list = 1
       }
       END { exit !found }' pnpm-workspace.yaml; then
    WORKSPACE_INFO="workspace"
  elif command -v jq &>/dev/null && jq -e '.workspaces | if type == "array" then length > 0
       elif type == "object" then (.packages | type == "array" and length > 0) else false end' package.json &>/dev/null; then
    WORKSPACE_INFO="workspace"
  fi
elif [[ -f "go.work" ]]; then
  WORKSPACE_INFO="go.work"
elif [[ -f "Cargo.toml" ]] && grep -q '^\[workspace\]' Cargo.toml 2>/dev/null; then
  WORKSPACE_INFO="Cargo workspace"
fi

# ── File helpers ──────────────────────────────────────────────────────────────

# Generated/build directories. .git is audit-only: clean commands preserve it by default.
GENERATED_DIR_PAT="${CLEAN_DIR_TARGETS_REGEX}|\.git"

# Lockfiles, generated files, and binary/asset files to exclude from analysis
GENERATED_FILE_PAT="${CLEAN_FILE_TARGETS_REGEX}|package-lock\.json|pnpm-lock\.yaml|bun\.lock|bun\.lockb|yarn\.lock|\.min\.(js|css)|CHANGELOG|CHANGES|\.(png|jpg|jpeg|gif|webp|ico|svg|woff2?|ttf|eot|pdf|mp4|mp3|wav|zip|tar|gz|snap|map)$"

# Package manifest files — change frequently on dep bumps; not useful hotspot signals
# Covers: npm/pnpm/bun, Cargo, Python, Go, Ruby, Java/Kotlin, .NET, PHP, Swift, Scala
MANIFEST_PAT="package\.json|pnpm-workspace\.yaml|Cargo\.toml|pyproject\.toml|setup\.(py|cfg)|Pipfile|go\.(mod|sum)|Gemfile|pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|composer\.json|Package\.swift|build\.sbt|requirements[^/]*\.txt|[^/]+\.(csproj|fsproj|vbproj)$"

# Clean tracked files: git ls-files excludes .gitignored files automatically.
# Use (^|/) so generated dirs are excluded at any depth (handles monorepos).
SOURCE_PAT='\.(js|jsx|ts|tsx|vue|svelte|astro|mjs|cjs|py|go|rs|kt|kts|java|rb|sh|bash|zsh|cs|swift|php|scala|c|cpp|h|hpp)$'
CLEAN_FILES=()
SKIPPED_FILES=0
git ls-files -z > "$AUDIT_TMP/tracked" || exit 1
while IFS= read -r -d '' file; do
  [[ "$file" =~ (^|/)($GENERATED_DIR_PAT)/ || "$file" =~ ($GENERATED_FILE_PAT) ]] && continue
  parent="$file"
  skip=false
  while :; do
    [[ -L "./$parent" ]] && { skip=true; break; }
    [[ "$parent" == */* ]] || break
    parent="${parent%/*}"
  done
  if $skip || [[ ! -f "./$file" ]]; then
    SKIPPED_FILES=$((SKIPPED_FILES + 1))
    continue
  fi
  CLEAN_FILES+=("$file")
done < "$AUDIT_TMP/tracked"

count_files() {
  local pattern="$1" count=0 i
  for ((i = 0; i < ${#CLEAN_FILES[@]}; i++)); do
    [[ "${CLEAN_FILES[$i]}" =~ $pattern ]] && count=$((count + 1))
  done
  echo "$count"
}

if [[ "$WORKSPACE_INFO" == workspace ]]; then
  PKG_COUNT=$(count_files '/package\.json$')
  WORKSPACE_INFO="workspace (${PKG_COUNT} tracked package manifests)"
fi

# ── Header ────────────────────────────────────────────────────────────────────

HEADER_META=("$REPO_NAME")
$HAS_PACKAGE_JSON && HEADER_META+=("Package manager: ${PM}")
[[ -n "$WORKSPACE_INFO" ]] && HEADER_META+=("$WORKSPACE_INFO")
HEADER_META+=("Lookback: ${SINCE}")
out_context "${HEADER_META[@]}"

# ══════════════════════════════════════════════════════════════════════════════
# 1. GIT SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
section_start "Git Signals"

if git rev-parse --verify --quiet HEAD >/dev/null 2>"$AUDIT_TMP/stderr"; then
# Churn — files with the most edits; high churn relative to total commits suggests
# a hotspot: either critical shared code or a file with design/stability issues
# alias: git churn
HISTORY=$(read_git log --no-merges --oneline --since="$SINCE") || exit 1
TOTAL_COMMITS_SINCE=$(printf '%s\n' "$HISTORY" | grep -c . || true)
CHURN_DATA=$(read_git log --no-merges --format=format: --name-only --since="$SINCE") || exit 1
CHURN_LIST=$(printf '%s\n' "$CHURN_DATA" \
  | grep -Ev "(^|/)($GENERATED_DIR_PAT)/" \
  | grep -Ev "($GENERATED_FILE_PAT)" \
  | grep -Ev "(^|/)($MANIFEST_PAT)" \
  | grep -v '^$' \
  | sort | uniq -c | sort -nr | head -10 || true)
if [[ -n "$CHURN_LIST" && "$TOTAL_COMMITS_SINCE" -gt 0 ]]; then
  TOP_CHURN_COUNT=$(echo "$CHURN_LIST" | awk 'NR==1{print $1}')
  TOP_CHURN_FILE=$(echo "$CHURN_LIST" | awk 'NR==1{$1=""; sub(/^ /,""); print}')
  TOP_CHURN_PCT=$(( (TOP_CHURN_COUNT * 100) / TOTAL_COMMITS_SINCE ))
  if [[ $TOP_CHURN_PCT -ge 30 ]]; then
    warn "Top churn: ${TOP_CHURN_FILE} changed in ${TOP_CHURN_PCT}% of commits — likely a hotspot"
  elif [[ $TOP_CHURN_PCT -ge 15 ]]; then
    warn "Top churn: ${TOP_CHURN_FILE} changed in ${TOP_CHURN_PCT}% of commits — worth watching"
  else
    ok "Top churn: ${TOP_CHURN_FILE} at ${TOP_CHURN_PCT}% — no dominant hotspot"
  fi
  info "Top 10 most-changed files (since ${SINCE})"
  while IFS= read -r line; do row "$line"; done <<< "$CHURN_LIST"
else
  info "Top 10 most-changed files (since ${SINCE})"
  row "(no data)"
fi

# Knowledge concentration — top committer's share of total commits; a high share means
# most codebase knowledge lives with one person, which is a risk if they leave.
# A repository without a remote has no other contributors, so it is skipped there.
# alias: git stats [--since="6 months ago"]
ALL_HISTORY=$(read_git log --no-merges --oneline) || exit 1
TOTAL_COMMITS=$(printf '%s\n' "$ALL_HISTORY" | grep -c . || true)
if [[ "$TOTAL_COMMITS" -gt 0 ]]; then
  AUTHORS=$(read_git shortlog -sn --no-merges HEAD) || exit 1
  TOP_LINE=$(printf '%s\n' "$AUTHORS" | head -1)
  TOP_COUNT=$(echo "$TOP_LINE" | awk '{print $1}')
  TOP_NAME=$(echo "$TOP_LINE" | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//')
  if [[ -z "$HAS_REMOTE" ]]; then
    info "No remote — knowledge concentration check skipped"
  else
    PCT=$(( (TOP_COUNT * 100) / TOTAL_COMMITS ))
    if [[ $PCT -ge 60 ]]; then
      warn "Knowledge concentration: ${TOP_NAME} wrote ${PCT}% of all commits — single-author risk"
    elif [[ $PCT -ge 40 ]]; then
      warn "Knowledge concentration: ${TOP_NAME} at ${PCT}% (${TOP_COUNT}/${TOTAL_COMMITS} commits)"
    else
      ok "Knowledge concentration: ${TOP_NAME} at ${PCT}% — healthy spread"
    fi
  fi
  info "Top 5 contributors"
  while IFS= read -r line; do row "$line"; done < <(printf '%s\n' "$AUTHORS" | head -5)
fi

# Bug clusters — files that repeatedly appear in bug-fix commits; a file appearing
# in many bug-fix commits suggests it is fragile, hard to change correctly, or under-tested
# alias: git bugspots
BUG_HISTORY=$(read_git log --no-merges -i -E --grep='fix|bug|broken' --format='%H' --since="$SINCE") || exit 1
BUG_FIX_COMMITS=$(printf '%s\n' "$BUG_HISTORY" | grep -c . || true)
BUG_FIX_COMMITS=${BUG_FIX_COMMITS:-0}
BUG_DATA=$(read_git log --no-merges -i -E --grep='fix|bug|broken' --name-only --format='' --since="$SINCE") || exit 1
BUG_LIST=$(printf '%s\n' "$BUG_DATA" \
  | grep -Ev "(^|/)($GENERATED_DIR_PAT)/" \
  | grep -Ev "($GENERATED_FILE_PAT)" \
  | grep -v '^$' \
  | sort | uniq -c | sort -nr | head -10 || true)
if [[ "$TOTAL_COMMITS_SINCE" -gt 0 ]]; then
  BUG_PCT=$(( (BUG_FIX_COMMITS * 100) / TOTAL_COMMITS_SINCE ))
  if [[ $BUG_PCT -ge 25 ]]; then
    warn "${BUG_FIX_COMMITS} fix-labelled commits (${BUG_PCT}% of total) — review maintenance load"
  elif [[ $BUG_PCT -ge 10 ]]; then
    warn "${BUG_FIX_COMMITS} bug-fix commits (${BUG_PCT}% of total)"
  elif [[ $BUG_FIX_COMMITS -gt 0 ]]; then
    ok "${BUG_FIX_COMMITS} bug-fix commits (${BUG_PCT}% of total) — normal"
  else
    ok "No bug-fix commits found — either clean or low commit message discipline"
  fi
fi
if [[ -n "$BUG_LIST" ]]; then
  info "Top 10 files in bug-fix commits"
  while IFS= read -r line; do row "$line"; done <<< "$BUG_LIST"
fi

# Velocity — monthly commit count reveals whether the codebase is accelerating, declining,
# or stagnant; compare recent months to the 12-month average to spot trends
# alias: git pulse
VELOCITY_MONTHS=""
for ((offset = 12; offset >= 1; offset--)); do
  month=$(date -v1d -v-"${offset}"m +%Y-%m)
  VELOCITY_MONTHS+="${VELOCITY_MONTHS:+ }$month"
done
VELOCITY_START=$(date -v1d -v-12m +%Y-%m-01)
VELOCITY_END=$(date -v1d +%Y-%m-01)
VELOCITY_RAW=$(read_git log --no-merges --format='%ad' --date=format:'%Y-%m' \
  --since="$VELOCITY_START 00:00:00" --before="$VELOCITY_END 00:00:00") || exit 1
VELOCITY_DATA=$(printf '%s\n' "$VELOCITY_RAW" | awk -v months="$VELOCITY_MONTHS" '
      { counts[$0]++ }
      END {
        n = split(months, ordered, " ")
        for (i = 1; i <= n; i++) printf "%5d %s\n", counts[ordered[i]], ordered[i]
      }')
if [[ -n "$VELOCITY_DATA" ]]; then
  MONTH_COUNT=$(echo "$VELOCITY_DATA" | wc -l | tr -d ' ')
  AVG_COMMITS=$(echo "$VELOCITY_DATA" | awk '{sum+=$1} END{printf "%d", (NR>0 ? sum/NR : 0)}')
  # Recent 3-month average vs full-period average to detect trend
  RECENT_AVG=$(echo "$VELOCITY_DATA" | tail -3 | awk '{sum+=$1} END{printf "%d", (NR>0 ? sum/NR : 0)}')
  if [[ $MONTH_COUNT -ge 4 && $AVG_COMMITS -gt 0 ]]; then
    if [[ $((RECENT_AVG * 100)) -ge $((AVG_COMMITS * 130)) ]]; then
      VELOCITY_TREND="accelerating"
      ok "Velocity: accelerating — recent avg ${RECENT_AVG}/mo vs 12-mo avg ${AVG_COMMITS}/mo"
    elif [[ $((RECENT_AVG * 100)) -le $((AVG_COMMITS * 70)) ]]; then
      VELOCITY_TREND="slowing"
      warn "Velocity: slowing — recent avg ${RECENT_AVG}/mo vs 12-mo avg ${AVG_COMMITS}/mo"
    else
      VELOCITY_TREND="steady"
      ok "Velocity: steady — recent avg ${RECENT_AVG}/mo vs 12-mo avg ${AVG_COMMITS}/mo"
    fi
  fi
  info "Monthly commit velocity (last 12 complete months, including inactive months)"
  while IFS= read -r line; do row "$line"; done <<< "$VELOCITY_DATA"
fi

# Firefighting — commits with revert/hotfix/emergency/rollback in the message signal reactive work
# alias: git firefighting
FIRE_HISTORY=$(read_git log --no-merges -i -E --grep='revert|hotfix|emergency|rollback' \
  --format='%H' --since="$SINCE") || exit 1
FIRE_COUNT=$(printf '%s\n' "$FIRE_HISTORY" | grep -c . || true)
FIRE_COUNT=${FIRE_COUNT:-0}
if [[ "$FIRE_COUNT" -eq 0 ]]; then
  ok "Firefighting: 0 hotfix/revert commits in period"
elif [[ "$FIRE_COUNT" -lt 5 ]]; then
  warn "Firefighting: ${FIRE_COUNT} hotfix/revert/emergency commits in period"
else
  warn "Firefighting: ${FIRE_COUNT} hotfix/revert/emergency commits — possible instability"
fi
else
  if [[ -s "$AUDIT_TMP/stderr" ]]; then
    fail "Could not read HEAD — Git history checks skipped"
    command_error
  else
    info "No readable HEAD — Git history checks skipped"
  fi
fi

section_end

# ── Stale branches and worktrees (read-only; uses the last fetch) ────────────
section_start "Stale Branches & Worktrees"

# shellcheck disable=SC2034  # read by lib/stale-branches.sh
SB_TMP=$AUDIT_TMP
# shellcheck disable=SC2034  # read by lib/stale-branches.sh
SB_INVOKING_TOP=$(pwd -P)
if $NO_PACKAGE_MANAGER; then
  info "Stale-branch classification skipped (--no-package-manager)"
elif [[ -z "$HAS_REMOTE" ]]; then
  info "No remote — stale branch check skipped"
elif ! SB_GIT_DIR=$(sb_common_dir "$GIT_ROOT"); then
  SB_ERR=$(git -C "$GIT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>&1 >/dev/null | head -1)
  fail "Could not resolve the Git directory${SB_ERR:+ ($SB_ERR)} — stale branch check skipped"
else
  STALE_PLAN=$(sb_classify "$SB_GIT_DIR/$SB_SNAPSHOT_FILE")
  stale_rows() {
    local kind name _ detail wt
    while IFS="$SB_SEP" read -r kind name _ detail wt; do
      [[ "$kind" == "$1" ]] || continue
      [[ "$name" == /* ]] && name=$(sb_short_path "$name")
      row "${name} — ${detail}"
      [[ -n "$wt" ]] && row "  └ worktree $(sb_short_path "$wt")"
    done <<< "$STALE_PLAN"
  }
  stale_count() { printf '%s\n' "$STALE_PLAN" | awk -F "$SB_SEP" -v k="$1" '$1 == k { n++ } END { print n + 0 }'; }
  STALE_DELETE=$(stale_count delete)
  STALE_BLOCKED=$(stale_count blocked)
  STALE_REVIEW=$(( $(stale_count review) + $(stale_count unverified) ))
  STALE_HINTS=$(stale_count hint)
  if [[ "$STALE_DELETE" -gt 0 ]]; then
    warn "${STALE_DELETE} branch(es) whose remote branch was deleted can be pruned (run prune_branches)"
    stale_rows delete
  fi
  if [[ "$STALE_BLOCKED" -gt 0 ]]; then
    warn "${STALE_BLOCKED} stale branch(es) kept by a worktree with local changes or a checkout"
    stale_rows blocked
  fi
  if [[ "$STALE_REVIEW" -gt 0 ]]; then
    warn "${STALE_REVIEW} branch(es) lost their remote branch but have commits not confirmed on a remote"
    stale_rows review
    stale_rows unverified
  fi
  if [[ $((STALE_DELETE + STALE_BLOCKED + STALE_REVIEW)) -eq 0 ]]; then
    ok "No branches with a deleted remote branch"
  fi
  if [[ "$STALE_HINTS" -gt 0 ]]; then
    info "${STALE_HINTS} branch(es) or worktree(s) look stale but are kept"
    stale_rows hint
  fi
  info "${DIM}Based on remote-tracking refs from the last fetch${RESET}"
fi

section_end

# ══════════════════════════════════════════════════════════════════════════════
# 2. SECURITY
# ══════════════════════════════════════════════════════════════════════════════
section_start "Security"

if ! $HAS_PACKAGE_JSON; then
  info "Security audit skipped — not a Node.js project"
elif $NO_PACKAGE_MANAGER; then
  info "Security audit skipped (--no-package-manager)"
elif $SKIP_AUDIT; then
  info "Security audit skipped (--skip-security)"
elif [[ ${#LOCKFILES[@]} -eq 0 ]]; then
  info "Security audit skipped — no lockfile found"
elif [[ "$PM" != "npm" && "$PM" != "pnpm" ]]; then
  warn "Structured ${PM} auditing is not implemented here — run '${PM} audit' manually"
else
  out_progress "Running $PM audit…"
  AUDIT_OUTPUT=$("$PM" audit --json 2>"$AUDIT_TMP/stderr")
  AUDIT_STATUS=$?
  out_progress_done
  AUDIT_COUNTS=$(printf '%s\n' "$AUDIT_OUTPUT" | jq -er '
    select(has("error") | not) | .metadata.vulnerabilities | select(type == "object")
    | [.critical, .high, .moderate, .low]
    | select(all(.[]; type == "number" and . >= 0 and . <= 2147483647 and floor == .))
    | @tsv
  ' 2>/dev/null)
  if [[ $? -eq 0 && $AUDIT_STATUS -le 1 ]]; then
    read -r CRITICAL HIGH MODERATE LOW <<< "$AUDIT_COUNTS"
    [[ "$CRITICAL" -gt 0 ]] && fail "Critical vulnerabilities: ${CRITICAL} — fix immediately"
    [[ "$HIGH" -gt 0 ]]     && fail "High vulnerabilities: ${HIGH}"
    [[ "$MODERATE" -gt 0 ]] && warn "Moderate vulnerabilities: ${MODERATE}"
    [[ "$LOW" -gt 0 ]]      && info "Low vulnerabilities: ${LOW}"
    if [[ "$CRITICAL" -eq 0 && "$HIGH" -eq 0 && "$MODERATE" -eq 0 && "$LOW" -eq 0 ]]; then
      if [[ $AUDIT_STATUS -eq 0 ]]; then
        ok "No known vulnerabilities"
      else
        fail "Audit could not complete (exit ${AUDIT_STATUS})"
        command_error
      fi
    fi
  else
    fail "Audit could not be evaluated (exit ${AUDIT_STATUS}; missing or invalid vulnerability counts)"
    command_error
    [[ -n "$AUDIT_OUTPUT" ]] && row "$AUDIT_OUTPUT"
  fi
fi

section_end

# ══════════════════════════════════════════════════════════════════════════════
# 3. DEPENDENCY HEALTH
# ══════════════════════════════════════════════════════════════════════════════
section_start "Dependency Health"

if ! $HAS_PACKAGE_JSON; then
  info "Dependency health skipped — not a Node.js project"
else

# Multiple lockfiles
if [[ ${#LOCKFILES[@]} -gt 1 ]]; then
  warn "Multiple lockfiles: ${LOCKFILES[*]} — indicates package manager confusion"
elif [[ ${#LOCKFILES[@]} -eq 1 ]]; then
  ok "Single lockfile: ${LOCKFILES[0]}"
else
  warn "No lockfile found — dependencies may not be reproducible"
fi

# packageManager takes precedence over lockfiles during detection.
if command -v jq &>/dev/null; then
  if [[ ${#LOCKFILES[@]} -eq 1 && -n "$PKG_MGR_FIELD" ]]; then
    case "${LOCKFILES[0]}" in
      package-lock.json) LOCKFILE_PM="npm" ;;
      pnpm-lock.yaml) LOCKFILE_PM="pnpm" ;;
      yarn.lock) LOCKFILE_PM="yarn" ;;
      bun.lock) LOCKFILE_PM="bun" ;;
    esac
    if [[ "$PKG_MGR_FIELD" != "$LOCKFILE_PM" ]]; then
      warn "packageManager field says '${PKG_MGR_FIELD}' but lockfile belongs to '${LOCKFILE_PM}'"
    fi
  fi
  if [[ -z "$PKG_MGR_FIELD" ]]; then
    info "No packageManager field — not pinned via corepack"
  fi

  # engines.node
  ENGINES_NODE=$(jq -r '.engines.node // empty' package.json 2>/dev/null || true)
  if $NO_PACKAGE_MANAGER; then
    CURRENT_NODE="not checked (--no-package-manager)"
  else
    CURRENT_NODE=$(node -v 2>/dev/null | sed 's/v//' || true)
  fi
  if [[ -n "$ENGINES_NODE" ]]; then
    info "engines.node: ${ENGINES_NODE} (current: ${CURRENT_NODE:-unavailable}; compatibility not evaluated)"
  else
    warn "No engines.node in package.json — Node version requirement undeclared"
  fi

  # Dep count
  DEP_COUNT=$(jq '[(.dependencies // {}), (.devDependencies // {})] | add | length' package.json 2>/dev/null || echo "?")
  info "Total declared deps: ${DEP_COUNT}"

  # Vendored Node runtime — when the active Node fails devEngines.runtime and
  # onFail is "download", pnpm installs its own build and links it into
  # node_modules/.bin, which it prepends to the PATH of every child process.
  # Older pnpm links that build out of the global store; sandboxes commonly deny
  # execute there, so anything resolving a bare `node` fails with a message that
  # names neither the runtime nor the sandbox.
  DEV_ENGINES_ONFAIL=$(jq -r '.devEngines.runtime.onFail // empty' package.json 2>/dev/null || true)
  RUNTIME_ON_FAIL=$(grep -E '^runtimeOnFail[[:space:]]*:' pnpm-workspace.yaml 2>/dev/null | head -1 \
    | sed -e 's/.*:[[:space:]]*//' -e 's/#.*//' -e 's/["'"'"']//g' -e 's/[[:space:]]*$//' || true)
  VENDORED_NODE=""
  for candidate in node_modules/node node_modules/.bin/node; do
    [[ -e "$candidate" || -L "$candidate" ]] || continue
    VENDORED_NODE=$(realpath "$candidate" 2>/dev/null) \
      || VENDORED_NODE=$(readlink "$candidate" 2>/dev/null) \
      || VENDORED_NODE="$candidate"
    break
  done
  if [[ -n "$VENDORED_NODE" ]]; then
    REPO_REAL=$(realpath "$GIT_ROOT" 2>/dev/null || printf '%s' "$GIT_ROOT")
    if [[ "$VENDORED_NODE" == "$REPO_REAL"/* ]]; then
      info "Vendored Node runtime in node_modules, kept inside the repository"
    else
      warn "Vendored Node runtime resolves outside the repository — sandboxes may deny executing it"
      row "$VENDORED_NODE"
      row "Remove with: rm -f node_modules/node node_modules/.bin/node"
    fi
  fi
  if [[ "$DEV_ENGINES_ONFAIL" == "download" && "$RUNTIME_ON_FAIL" != "ignore" ]]; then
    info "devEngines.runtime.onFail=download — pnpm installs its own Node when the active version is out of range"
    row "Set runtimeOnFail: ignore in pnpm-workspace.yaml to keep the version manager's Node"
  fi
fi

# Outdated
if $NO_PACKAGE_MANAGER; then
  info "Outdated check skipped (--no-package-manager)"
elif [[ "$PM" != "npm" && "$PM" != "pnpm" ]]; then
  info "Structured ${PM} outdated checks are not implemented here"
else
  out_progress "Running $PM outdated…"
  OUTDATED_OUTPUT=$("$PM" outdated --json 2>"$AUDIT_TMP/stderr")
  OUTDATED_STATUS=$?
  out_progress_done
  OUTDATED_COUNT=$(printf '%s\n' "$OUTDATED_OUTPUT" | jq -er '
    select(type == "object" and (has("error") | not))
    | select(all(.[]; type == "object" and (.latest | type == "string")))
    | length
  ' 2>/dev/null)
  if [[ $? -ne 0 || $OUTDATED_STATUS -gt 1 || ( $OUTDATED_STATUS -ne 0 && "$OUTDATED_COUNT" == 0 ) ]]; then
    fail "Outdated check could not be evaluated (exit ${OUTDATED_STATUS}; missing or invalid package data)"
    command_error
    [[ -n "$OUTDATED_OUTPUT" ]] && row "$OUTDATED_OUTPUT"
  elif [[ "$OUTDATED_COUNT" -gt 0 ]]; then
    warn "${OUTDATED_COUNT} outdated package(s)"
  else
    ok "All dependencies appear up-to-date"
  fi
fi

# minimum-release-age / minimumReleaseAge — cooldown before a newly published
# package can be installed; protects against supply chain attacks.
# Key name varies by PM: pnpm uses "minimum-release-age" in .npmrc or pnpm-workspace.yaml,
#                        npm uses "min-release-age" in .npmrc (days),
#                        bun uses "minimumReleaseAge" in bunfig.toml (seconds integer).
report_cooldown() {
  local value="$1" source="$2" unit="$3" mins="" number suffix
  value=$(printf '%s\n' "$value" | sed "s/[[:space:]]*#.*//; s/[\"']//g; s/^[[:space:]]*//; s/[[:space:]]*$//")
  if [[ "$value" =~ ^([0-9]{1,9})([[:space:]]+[[:alpha:]]+)?$ ]]; then
    number="${BASH_REMATCH[1]}"
    suffix="${BASH_REMATCH[2]:-}"
    number=$((10#$number))
    case "$unit" in
      seconds) mins=$(parse_duration_minutes "${number}${suffix}") ;;
      days) [[ -z "$suffix" ]] && mins=$((number * 1440)) ;;
      minutes) [[ -z "$suffix" ]] && mins="$number" ;;
    esac
  fi
  if [[ -z "$mins" ]]; then
    warn "Unrecognized release-age cooldown '$value' in $source"
  elif [[ "$mins" -eq 0 ]]; then
    warn "Release-age cooldown is disabled or below one minute ($source)"
  else
    ok "Release-age cooldown: $(format_duration "$mins") ($source)"
  fi
}

if [[ "$PM" == "bun" ]]; then
  MRA_VAL=$(read_bunfig_mra bunfig.toml 2>/dev/null || true)
  MRA_SRC="bunfig.toml"
  if [[ -z "$MRA_VAL" ]]; then
    MRA_VAL=$(read_bunfig_mra "$HOME/.bunfig.toml" 2>/dev/null || true)
    # shellcheck disable=SC2088  # display label
    MRA_SRC="~/.bunfig.toml"
  fi
  if [[ -n "$MRA_VAL" ]]; then
    report_cooldown "$MRA_VAL" "$MRA_SRC" seconds
  else
    warn "No release-age cooldown found in project or home bunfig.toml"
  fi
elif [[ "$PM" == "pnpm" ]]; then
  MRA_NPMRC=$(grep -E '^minimum-release-age[[:space:]]*=' .npmrc 2>/dev/null | head -1 | sed 's/.*=[[:space:]]*//' || true)
  MRA_WS=$(grep -E '^minimumReleaseAge[[:space:]]*:' pnpm-workspace.yaml 2>/dev/null | head -1 | sed 's/.*:[[:space:]]*//' || true)
  if [[ -n "$MRA_WS" ]]; then
    report_cooldown "$MRA_WS" pnpm-workspace.yaml minutes
  elif [[ -n "$MRA_NPMRC" ]]; then
    report_cooldown "$MRA_NPMRC" .npmrc minutes
  elif $NO_PACKAGE_MANAGER; then
    info "pnpm global cooldown skipped (--no-package-manager)"
  elif MRA_VAL=$(pnpm config get minimumReleaseAge --global 2>"$AUDIT_TMP/stderr"); then
    if [[ -n "$MRA_VAL" && "$MRA_VAL" != "undefined" && "$MRA_VAL" != "null" ]]; then
      report_cooldown "$MRA_VAL" "pnpm global config" minutes
    else
      warn "No release-age cooldown found in project or pnpm global config"
    fi
  else
    fail "Could not read pnpm global cooldown configuration"
    command_error
  fi
elif [[ "$PM" == "npm" ]]; then
  # npm
  MRA_PAT='^(minimum-release-age|min-release-age)[[:space:]]*='
  MRA_VAL=$(grep -E "$MRA_PAT" .npmrc 2>/dev/null | head -1 | sed 's/.*=[[:space:]]*//' || true)
  MRA_SRC=".npmrc"
  if [[ -z "$MRA_VAL" ]]; then
    MRA_VAL=$(grep -E "$MRA_PAT" "$HOME/.npmrc" 2>/dev/null | head -1 | sed 's/.*=[[:space:]]*//' || true)
    # shellcheck disable=SC2088  # display label
    MRA_SRC="~/.npmrc"
  fi
  if [[ -n "$MRA_VAL" ]]; then
    report_cooldown "$MRA_VAL" "$MRA_SRC" days
  else
    warn "No release-age cooldown found in project or home .npmrc"
  fi
else
  info "Cooldown checks for ${PM} are not implemented here"
fi

fi # HAS_PACKAGE_JSON

section_end

# ══════════════════════════════════════════════════════════════════════════════
# 4. PROJECT STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════
section_start "Project Structure"

TRACKED_FILES=${#CLEAN_FILES[@]}
SOURCE_FILES=$(count_files "$SOURCE_PAT")
TOTAL_LINES=0
TODO_TOTAL=0
TODO_FILES=()
ENV_FILES=0
UNREADABLE_FILES=0
: > "$AUDIT_TMP/sizes"
for ((i = 0; i < ${#CLEAN_FILES[@]}; i++)); do
  file="${CLEAN_FILES[$i]}"
  # Environment files can hold secrets and add nothing to size or marker counts.
  if [[ "${file##*/}" =~ ^\.env($|\.) ]]; then
    ENV_FILES=$((ENV_FILES + 1))
    continue
  fi
  # stderr is redirected before the input so a failed open is captured too.
  if ! lines=$(wc -l 2>"$AUDIT_TMP/stderr" < "./$file"); then
    if grep -qE 'Operation not permitted|Permission denied' "$AUDIT_TMP/stderr"; then
      UNREADABLE_FILES=$((UNREADABLE_FILES + 1))
    else
      fail "Could not count lines in $(printf '%q' "$file")"
    fi
    continue
  fi
  TOTAL_LINES=$((TOTAL_LINES + lines))
  if [[ "$file" =~ $SOURCE_PAT ]]; then
    printf '%s\t%q\n' "$lines" "$file" >> "$AUDIT_TMP/sizes"
  fi
  markers=$(grep -I -oE "TODO|FIXME|HACK|XXX" "./$file" | wc -l)
  marker_status=$?
  if [[ $marker_status -gt 1 ]]; then
    fail "Could not inspect markers in $(printf '%q' "$file")"
  elif [[ "$markers" -gt 0 ]]; then
    TODO_TOTAL=$((TODO_TOTAL + markers))
    TODO_FILES+=("$file")
  fi
done
info "${TRACKED_FILES} tracked files | ${SOURCE_FILES} source files | ~${TOTAL_LINES} lines"
[[ $SKIPPED_FILES -gt 0 ]] && info "Skipped ${SKIPPED_FILES} symlink, missing or non-regular tracked path(s)"
[[ $ENV_FILES -gt 0 ]] && info "Skipped ${ENV_FILES} tracked .env file(s) without reading them; they may hold secrets"
[[ $UNREADABLE_FILES -gt 0 ]] && info "Skipped ${UNREADABLE_FILES} file(s) without read permission (for example a sandbox); line and marker counts exclude them"

# Top 10 largest source files (god component candidates)
info "Top 10 largest source files"
while IFS=$'\t' read -r lines file; do
  if [[ "$lines" -gt 500 ]]; then
    BIG_FILES=$(( BIG_FILES + 1 ))
    SECTION_OUTPUT+=("    ${YELLOW}${lines} lines  ${file}${RESET}")
  else
    row "${lines} lines  ${file}"
  fi
done < <(sort -rn "$AUDIT_TMP/sizes" | head -10)
if [[ "$BIG_FILES" -ge 3 ]]; then
  warn "${BIG_FILES} files over 500 lines in top 10 — likely god components, consider splitting"
elif [[ "$BIG_FILES" -gt 0 ]]; then
  info "${BIG_FILES} file(s) over 500 lines in top 10 — worth a review"
fi

# TODO/FIXME/HACK/XXX
TODO_FILE_COUNT=${#TODO_FILES[@]}
if [[ "$TODO_TOTAL" -gt 50 ]]; then
  warn "${TODO_TOTAL} TODO/FIXME/HACK/XXX markers across ${TODO_FILE_COUNT} files"
elif [[ "$TODO_TOTAL" -gt 0 ]]; then
  info "${TODO_TOTAL} TODO/FIXME/HACK/XXX across ${TODO_FILE_COUNT} files"
else
  ok "No TODO/FIXME/HACK/XXX markers"
fi
for ((i = 0; i < ${#TODO_FILES[@]}; i++)); do
  row "$(printf '%q' "${TODO_FILES[$i]}")"
done

# TypeScript ratio — only relevant for JS/TS projects
if $HAS_PACKAGE_JSON; then
  TS_COUNT=$(count_files '\.(ts|tsx)$')
  JS_COUNT=$(count_files '\.(js|jsx|mjs|cjs)$')
  TOTAL_JS_TS=$((TS_COUNT + JS_COUNT))
  if [[ "$TOTAL_JS_TS" -gt 0 ]]; then
    if [[ "$JS_COUNT" -eq 0 ]]; then
      ok "TypeScript: full TS codebase (${TS_COUNT} files)"
    else
      TS_PCT=$(( (TS_COUNT * 100) / TOTAL_JS_TS ))
      if [[ "$TS_PCT" -lt 50 ]]; then
        warn "Partial TypeScript: ${TS_PCT}% TS — ${TS_COUNT} .ts/tsx vs ${JS_COUNT} .js/jsx"
      else
        ok "Majority TypeScript: ${TS_PCT}% (${TS_COUNT} TS, ${JS_COUNT} JS)"
      fi
    fi
  fi
fi

section_end

# ══════════════════════════════════════════════════════════════════════════════
# 5. DOCUMENTATION FILES
# ══════════════════════════════════════════════════════════════════════════════
section_start "Documentation Files"

# macOS ignores case, so a misspelled name or link works locally and fails on
# Linux, CI and Git hosts; agents may also read a lowercase agents.md as AGENTS.md.
# Untracked files are included so mistakes surface before they are committed.
DOC_FINDINGS=0
DOC_UNREADABLE=0
DOC_ENV=0
if ! git ls-files -z -co --exclude-standard > "$AUDIT_TMP/doc-files.z"; then
  fail "Could not list files for documentation checks"
else
  tr '\0' '\n' < "$AUDIT_TMP/doc-files.z" > "$AUDIT_TMP/doc-files"
  awk -F/ '{ print; p = $1; for (i = 2; i <= NF; i++) { print p; p = p "/" $i } }' \
    "$AUDIT_TMP/doc-files" | sort -u > "$AUDIT_TMP/doc-paths"

  while IFS=$'\t' read -r path message; do
    if [[ "$path" == DESIGN-TOKENS ]]; then
      # An unreadable DESIGN.md is counted once, with the Markdown files below.
      if ! { : < "./$message"; } 2>/dev/null; then
        :
      elif ! awk 'NR == 1 && $0 != "---" { exit 1 } NR > 1 && $0 == "---" { exit 1 }
                NR > 1 && /^(colors|typography|rounded|spacing|components):/ { found = 1; exit }
                END { exit !found }' "./$message"; then
        warn "$(printf '%q' "$message") has no design tokens — agents read DESIGN.md as a visual design system; use another name (e.g. docs/architecture.md) for other design documents"
        DOC_FINDINGS=$((DOC_FINDINGS + 1))
      fi
      continue
    fi
    warn "$(printf '%q' "$path"): $message"
    DOC_FINDINGS=$((DOC_FINDINGS + 1))
  done < <(awk '
    {
      path = $0; n = split(path, part, "/"); base = part[n]; b = tolower(base)
      dir = n > 1 ? substr(path, 1, length(path) - length(base) - 1) : ""
      seen[tolower(path)] = seen[tolower(path)] ? seen[tolower(path)] "\t" path : path
      dot = index(b, "."); stem = dot ? substr(b, 1, dot - 1) : b; ext = dot ? substr(b, dot) : ""
      want = toupper(stem) ext
      if (base == "DESIGN.md") print "DESIGN-TOKENS\t" path
      if (b == "agents.md" || b == "claude.md" || b == "gemini.md") {
        if (base != want) print path "\tagents that look for " want " here may read this file on macOS but not elsewhere; rename it to " want " if it holds instructions, otherwise choose another name"
      } else if (b == "readme.md") {
        if (base != want) print path "\tname it README.md; Git hosts and tools expect that spelling"
      } else if (b == "copilot-instructions.md") {
        if (path != ".github/copilot-instructions.md") print path "\tCopilot reads only .github/copilot-instructions.md"
      } else if (dir == "" && b == "design.md") {
        if (base != want) print path "\tagents read DESIGN.md as a visual design system and on macOS would read this file; use DESIGN.md only for design tokens, otherwise another name (e.g. docs/architecture.md)"
      } else if ((dir == "" && b ~ /^(licen[cs]e(\.(md|txt))?|changelog\.md)$/) ||
                 ((dir == "" || dir == ".github") && b ~ /^(contributing|security|code_of_conduct)\.md$/)) {
        if (base != want) print path "\tname it " want "; Git hosts and tools expect that spelling"
      }
    }
    END {
      for (k in seen) if ((n = split(seen[k], same, "\t")) > 1) {
        others = same[2]; for (i = 3; i <= n; i++) others = others ", " same[i]
        print same[1] "\tdiffers only in case from " others "; only one survives a macOS checkout"
      }
    }
  ' "$AUDIT_TMP/doc-files" | sort)

  # Link targets may be .env files, but their contents are never opened.
  MD_FILES=()
  while IFS= read -r file; do
    [[ "$file" == *.md && -f "./$file" && ! -L "./$file" ]] || continue
    if [[ "${file##*/}" =~ ^\.env($|\.) ]]; then
      DOC_ENV=$((DOC_ENV + 1))
    elif ! { : < "./$file"; } 2>/dev/null; then
      DOC_UNREADABLE=$((DOC_UNREADABLE + 1))
    else
      MD_FILES+=("$file")
    fi
  done < "$AUDIT_TMP/doc-files"
  DOC_SCAN_FAILED=false
  if [[ ${#MD_FILES[@]} -gt 0 ]]; then
    if ! awk '
      function norm(p,   n, part, out, k, i, s) {
        n = split(p, part, "/"); k = 0
        for (i = 1; i <= n; i++) {
          if (part[i] == "" || part[i] == ".") continue
          if (part[i] == "..") { if (k == 0) return ""; k--; continue }
          out[++k] = part[i]
        }
        if (k == 0) return ""
        s = out[1]; for (i = 2; i <= k; i++) s = s "/" out[i]
        return s
      }
      function check(t,   p) {
        sub(/^</, "", t); sub(/>$/, "", t)
        if (t ~ /^#/ || t ~ /^[A-Za-z][A-Za-z0-9+.-]*:/ || t ~ /^\//) return
        sub(/[#?].*$/, "", t); gsub(/%20/, " ", t)
        if (t == "") return
        p = norm((dir == "" ? "" : dir "/") t)
        if (p == "" || p in exact) return
        if (tolower(p) in lower) print FILENAME "\t" t "\t" lower[tolower(p)]
      }
      NR == FNR { exact[$0] = 1; lower[tolower($0)] = $0; next }
      FNR == 1 { fence = 0; dir = FILENAME; if (!sub(/\/[^\/]*$/, "", dir)) dir = "" }
      /^[ \t]*(```|~~~)/ { fence = !fence; next }
      fence { next }
      {
        line = $0; gsub(/`[^`]*`/, "", line)
        if (match(line, /^[ \t]*\[[^\]]+\]:[ \t]*[^ \t]+/)) {
          t = substr(line, RSTART, RLENGTH); sub(/^[ \t]*\[[^\]]+\]:[ \t]*/, "", t); check(t)
        }
        while (match(line, /\]\([^) \t]+/)) {
          check(substr(line, RSTART + 2, RLENGTH - 2)); line = substr(line, RSTART + RLENGTH)
        }
      }
    ' "$AUDIT_TMP/doc-paths" "${MD_FILES[@]}" > "$AUDIT_TMP/doc-links" 2>"$AUDIT_TMP/stderr"; then
      DOC_SCAN_FAILED=true
      fail "Could not scan Markdown links: $(head -1 "$AUDIT_TMP/stderr")"
    fi
    while IFS=$'\t' read -r file target actual; do
      warn "$(printf '%q' "$file"): link to $target differs in case from $actual"
      DOC_FINDINGS=$((DOC_FINDINGS + 1))
    done < <(sort "$AUDIT_TMP/doc-links")
  fi
  [[ $DOC_ENV -gt 0 ]] && info "Skipped ${DOC_ENV} .env Markdown file(s) without reading them"
  if [[ $DOC_UNREADABLE -gt 0 ]]; then
    fail "Could not read ${DOC_UNREADABLE} documentation file(s); documentation checks are incomplete"
  elif [[ $DOC_FINDINGS -eq 0 ]] && ! $DOC_SCAN_FAILED; then
    ok "Conventional file names, placement and Markdown link casing look right"
  fi
fi

section_end

# Uses our installed validator, never code from the repository being audited.
section_start "Roadmap"
HAS_ROADMAP_CANDIDATE=false
for folder in . [Dd][Oo][Cc][Ss] [Tt][Aa][Ss][Kk][Ss] [Pp][Ll][Aa][Nn][Nn][Ii][Nn][Gg]; do
  [[ -d "$folder" ]] || continue
  for candidate in "$folder"/*; do
    [[ -f "$candidate" ]] || continue
    case "${candidate##*/}" in
      [Rr][Oo][Aa][Dd][Mm][Aa][Pp].[Mm][Dd]|[Rr][Oo][Aa][Dd][Mm][Aa][Pp][Ss].[Mm][Dd])
        HAS_ROADMAP_CANDIDATE=true ;;
    esac
  done
done
if ! $HAS_ROADMAP_CANDIDATE; then
  info "No skill-managed roadmap found"
elif python3 -I -B - "$SCRIPT_DIR" "$GIT_ROOT" > "$AUDIT_TMP/roadmaps" 2>"$AUDIT_TMP/stderr" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import roadmaps


def emit(kind, message):
    for number, line in enumerate(message.splitlines()):
        print(f"{kind if number == 0 else 'detail'}\t{line.strip()}")


problems = []
repos = roadmaps.repository_roadmaps(sys.argv[2], problems)
for problem in problems:
    emit("error", problem)
if not repos and not problems:
    emit("info", "No skill-managed roadmap found")
for repo in repos:
    try:
        brief_dir = Path(repo.root) / repo.dir
        env_files = [path for directory in (brief_dir, brief_dir / "archive")
                     if directory.is_dir() for path in directory.iterdir()
                     if path.suffix == ".md"
                     and (path.name == ".env.md" or path.name.startswith(".env."))]
        if env_files:
            emit("error", f"{repo.index}: skipped {len(env_files)} .env roadmap brief(s) "
                 "without reading them; roadmap validation is incomplete")
            continue
        report, _ = roadmaps.rm.check_report(repo)
    except (OSError, UnicodeDecodeError) as exc:
        emit("error", f"{repo.index}: cannot read the roadmap: {exc}")
        continue
    for error in report.errors:
        emit("error", error)
    for warning in report.warnings:
        emit("warning", warning)
    if not report.errors:
        emit("ok", f"{repo.index}: roadmap layout and briefs are valid")
PY
then
  while IFS=$'\t' read -r kind message; do
    case "$kind" in
      ok)      ok "$message" ;;
      warning) warn "$message" ;;
      error)   fail "$message" ;;
      detail)  row "$message" ;;
      info)    info "$message" ;;
    esac
  done < "$AUDIT_TMP/roadmaps"
else
  fail "Could not validate skill-managed roadmaps"
  command_error
fi
section_end

# ══════════════════════════════════════════════════════════════════════════════
# 6. TEST SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
section_start "Test Signals"

if $HAS_PACKAGE_JSON && command -v jq &>/dev/null; then
  # Detect test framework(s) — check root package.json AND any sub-package package.json files
  FRAMEWORKS=()
  for fw in vitest jest playwright cypress mocha jasmine; do
    for ((i = 0; i < ${#CLEAN_FILES[@]}; i++)); do
      file="${CLEAN_FILES[$i]}"
      [[ "${file##*/}" == package.json ]] || continue
      if jq -e --arg fw "$fw" '
        ((.dependencies // {}) + (.devDependencies // {})) | keys
        | any(. == $fw or . == ("@" + $fw + "/test") or . == ("@" + $fw + "/core"))
      ' "./$file" >/dev/null 2>&1; then
        FRAMEWORKS+=("$fw")
        break
      fi
    done
  done
  if [[ ${#FRAMEWORKS[@]} -gt 0 ]]; then
    ok "Test framework(s): ${FRAMEWORKS[*]}"
  else
    warn "No recognised test framework in any package.json"
  fi

  # Test script
  TEST_SCRIPT=$(jq -r '.scripts.test // empty' package.json 2>/dev/null || true)
  if [[ -z "$TEST_SCRIPT" ]]; then
    warn "No 'test' script in root package.json"
  # Only npm's generated placeholder and a bare "exit 1" are known to always fail.
  elif [[ "$TEST_SCRIPT" =~ ^echo[[:space:]]+(\"Error:\ no\ test\ specified\"|\'Error:\ no\ test\ specified\')[[:space:]]*\&\&[[:space:]]*exit[[:space:]]+1[[:space:]]*$ \
          || "$TEST_SCRIPT" =~ ^[[:space:]]*exit[[:space:]]+1[[:space:]]*$ ]]; then
    fail "test script is a placeholder: '${TEST_SCRIPT}'"
  else
    ok "test script: ${TEST_SCRIPT}"
  fi
fi

# Test file count — broad conventions: JS/TS, Go, Python, Ruby, Java/Kotlin, Rust
# alias: git churn (to see test files in churn)
TEST_FILES=$(count_files \
  "(^|/)test_[^/]+\.(py|rb)$|\
(^|/)[^/]+_test\.(go|py|rb|js|jsx|ts|tsx)$|\
(^|/)[^/]+\.(test|spec)\.(js|jsx|ts|tsx|vue|svelte)$|\
(^|/)[^/]+Test\.(java|kt|cs|php)$|\
(^|/)[^/]+_spec\.rb$|\
(^|/)tests?/[^/]+\.(py|rs|go|java|kt|rb|php|js|ts|tsx)$" \
)
if [[ "$SOURCE_FILES" -gt 0 ]]; then
  if [[ "$TEST_FILES" -eq 0 ]]; then
    warn "No test files found (by common naming conventions)"
  else
    TEST_RATIO=$(( (TEST_FILES * 100) / SOURCE_FILES ))
    if [[ "$TEST_RATIO" -lt 20 ]]; then
      warn "Low test ratio: ${TEST_FILES} test files / ${SOURCE_FILES} source files (${TEST_RATIO}%)"
    else
      ok "${TEST_FILES} test files / ${SOURCE_FILES} source files (${TEST_RATIO}% ratio)"
    fi
  fi
fi

# Coverage config — JS-specific (vitest/jest/vite config files)
if $HAS_PACKAGE_JSON; then
  HAS_COV_CONFIG=0
  for ((i = 0; i < ${#CLEAN_FILES[@]}; i++)); do
    file="${CLEAN_FILES[$i]}"
    if [[ "$file" =~ (^|/)(vitest|jest|vite)\.config\.(js|ts|mjs|cjs)$ ]] && grep -q "coverage" "./$file"; then
      HAS_COV_CONFIG=$((HAS_COV_CONFIG + 1))
    fi
  done
  if [[ "$HAS_COV_CONFIG" -gt 0 ]]; then
    ok "Coverage config found in test config"
  else
    warn "No coverage config found in vitest/jest config"
  fi
fi

section_end

# ══════════════════════════════════════════════════════════════════════════════
# 7. BUILD SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
section_start "Build Signals"

# CI config — regardless of language
if [[ -z "$HAS_REMOTE" ]]; then
  info "No remote — CI check skipped"
elif [[ -d ".github/workflows" ]] && git ls-files ".github/workflows/*.yml" ".github/workflows/*.yaml" 2>/dev/null | grep -q .; then
  CI_COUNT=$(git ls-files ".github/workflows/*.yml" ".github/workflows/*.yaml" 2>/dev/null | wc -l | tr -d ' ')
  ok "GitHub Actions: ${CI_COUNT} workflow(s)"
elif [[ -f ".travis.yml" || -f ".circleci/config.yml" || -f "Jenkinsfile" || -f ".gitlab-ci.yml" ]]; then
  ok "CI config found"
else
  warn "No CI config found"
fi

if [[ -f "Makefile" ]]; then
  info "Makefile present"
fi

if $HAS_PACKAGE_JSON; then
if command -v jq &>/dev/null; then
  BUILD_SCRIPT=$(jq -r '.scripts.build // empty' package.json 2>/dev/null || true)
  if [[ -n "$BUILD_SCRIPT" ]]; then
    ok "build script: ${BUILD_SCRIPT}"
  else
    warn "No 'build' script in package.json"
  fi
fi

# TypeScript config — check root first, then any sub-package tsconfig
TSCONFIG_FILE=""
[[ -f "tsconfig.json" ]] && TSCONFIG_FILE="tsconfig.json"
if [[ -z "$TSCONFIG_FILE" && "${TS_COUNT:-0}" -gt 0 ]]; then
  for ((i = 0; i < ${#CLEAN_FILES[@]}; i++)); do
    [[ "${CLEAN_FILES[$i]}" == */tsconfig.json ]] || continue
    TSCONFIG_FILE="${CLEAN_FILES[$i]}"
    break
  done
fi

if [[ -n "$TSCONFIG_FILE" ]]; then
  if command -v jq &>/dev/null; then
    STRICT=$(jq -r '.compilerOptions.strict // empty' "./$TSCONFIG_FILE" 2>/dev/null)
    STRICT_STATUS=$?
    if [[ "$STRICT" == "true" ]]; then
      ok "TypeScript: ${TSCONFIG_FILE} explicitly sets strict: true"
    elif [[ $STRICT_STATUS -ne 0 ]]; then
      info "TypeScript: ${TSCONFIG_FILE} is not plain JSON; effective strict mode not evaluated"
    else
      info "TypeScript: ${TSCONFIG_FILE} does not explicitly enable strict; inherited settings not evaluated"
    fi
  else
    ok "TypeScript config: ${TSCONFIG_FILE}"
  fi
elif [[ "${TS_COUNT:-0}" -gt 0 ]]; then
  warn "TypeScript files present but no tsconfig.json found"
fi

# Linter — check root and sub-packages
LINTER=""
for f in .eslintrc .eslintrc.js .eslintrc.cjs .eslintrc.json .eslintrc.yml .eslintrc.yaml \
         eslint.config.js eslint.config.ts eslint.config.mjs eslint.config.cjs; do
  if [[ -f "$f" ]] || git ls-files "*/$f" 2>/dev/null | grep -q .; then
    LINTER="ESLint" && break
  fi
done
if [[ -f "biome.json" || -f "biome.jsonc" ]] || git ls-files "*/biome.json" "*/biome.jsonc" 2>/dev/null | grep -q .; then
  LINTER="${LINTER:+${LINTER} + }Biome"
fi

if [[ -n "$LINTER" ]]; then
  ok "Linter: ${LINTER}"
else
  warn "No ESLint or Biome config found"
fi

# Formatter
FORMATTER=""
for f in .prettierrc .prettierrc.js .prettierrc.cjs .prettierrc.json .prettierrc.yml \
         prettier.config.js prettier.config.cjs prettier.config.mjs; do
  [[ -f "$f" ]] && FORMATTER="Prettier" && break
done
[[ -n "$FORMATTER" ]] && ok "Formatter: ${FORMATTER}" || info "No Prettier config (may be handled by Biome/ESLint)"

# .nvmrc/.node-version — pins Node version for fnm/nvm local dev
for _f in .nvmrc .node-version; do
  if [[ -f "$_f" ]]; then
    ok "Node version file: $(tr -d '[:space:]' < "$_f") (${_f})"
    break
  fi
done

fi # HAS_PACKAGE_JSON

section_end

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

# Collect notable signals for the one-liner verdict
SIGNALS=()

[[ "${VELOCITY_TREND}" == "slowing" ]]                          && SIGNALS+=("slowing activity")
[[ "${PCT:-0}" -ge 60 ]]                                        && SIGNALS+=("single-author risk")
[[ "${PCT:-0}" -ge 40 && "${PCT:-0}" -lt 60 ]]                  && SIGNALS+=("concentrated authorship")
[[ "${BUG_PCT:-0}" -ge 25 ]]                                    && SIGNALS+=("frequent fix-labelled commits")
[[ "${FIRE_COUNT:-0}" -ge 5 ]]                                   && SIGNALS+=("reactive workflow")
[[ "$SOURCE_FILES" -gt 0 && "${TEST_FILES:-0}" -eq 0 ]]           && SIGNALS+=("no tests found")
[[ "${TEST_FILES:-0}" -gt 0 && "${TEST_RATIO:-0}" -lt 20 ]]     && SIGNALS+=("sparse tests")
[[ "${BIG_FILES:-0}" -ge 3 ]]                                   && SIGNALS+=("${BIG_FILES} large files")

# Compose verdict — join signals with ", " using a loop (IFS trick drops the space)
SIGNAL_STR=""
for ((i = 0; i < ${#SIGNALS[@]}; i++)); do
  [[ -n "$SIGNAL_STR" ]] && SIGNAL_STR+=", "
  SIGNAL_STR+="${SIGNALS[$i]}"
done

if [[ "$ISSUES" -eq 0 && "$WARNINGS" -eq 0 && ${#SIGNALS[@]} -eq 0 ]]; then
  VERDICT="No notable signals in the checks performed"
elif [[ "$ISSUES" -gt 0 && ${#SIGNALS[@]} -eq 0 ]]; then
  VERDICT="Needs attention — see issues above"
elif [[ ${#SIGNALS[@]} -eq 0 ]]; then
  VERDICT="Some warnings, no critical signals"
elif [[ "$ISSUES" -ge 3 || ${#SIGNALS[@]} -ge 3 ]]; then
  VERDICT="Needs attention — ${SIGNAL_STR}"
else
  VERDICT="${SIGNAL_STR}"
fi

out_summary "$ISSUES" "$WARNINGS" "No issues or warnings"
out_note "${BOLD}${VERDICT}${RESET}"
echo ""

[[ "$ISSUES" -gt 0 ]] && exit 1
exit 0
