#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      python3 "$SCRIPT_DIR/dotfiles-help.py" --render-help "git amend-date" <<'EOF'
Usage: git amend-date <age | date> [commit]
       git amend-date --shift <age> [commit]

{summary}

An age is a non-negative integer with m, h, d or w (minutes, hours, days,
weeks). A day is exactly 24 hours. Bare ages mean ago from now; --shift
subtracts from each existing date independently. Explicit dates use Git's
date parser, e.g. "2026-10-30T12:00:00+01:00".

Defaults to HEAD; an optional hash or revision selects an ancestor on the
current branch. Rewrites that commit and its descendants, preserving their
dates except the selected commit's. Older-commit rewrites refuse merges
and require an attached branch. Other branches are not moved.

Previews dates and asks [y/N]. Warns if earlier than a parent or reachable
from local remote-tracking refs (no fetch; pushed status may be stale).
Commit contents, messages, identities, working tree and index are preserved.
No commit hooks run. Signed commits anywhere in the rewrite are refused.
Hashes change; published commits require coordinated rewriting.

  --shift     Move each existing date back by the given age
  -h, --help  Show help

{examples}
EOF
      exit $?
      ;;
  esac
done

mode=--ago-seconds
if [[ ${1:-} == --shift ]]; then
  mode=--shift-seconds
  shift
fi
if [[ $# -lt 1 || $# -gt 2 || -z $1 || $1 == -* || ${2:-HEAD} == -* || ( $# == 2 && -z $2 ) ]]; then
  echo "Error: expected an age or quoted date and optional commit; see git amend-date -h." >&2
  exit 2
fi
commit=${2:-HEAD}
# shellcheck source=lib/pm-utils.sh
source "$SCRIPT_DIR/lib/pm-utils.sh"
if seconds=$(parse_age_seconds "$1"); then
  exec python3 "$SCRIPT_DIR/git-amend-date.py" "$mode" "$seconds" "$commit"
fi
if [[ $mode == --shift-seconds || $1 =~ ^[0-9]+[[:alpha:]]?$ ]]; then
  echo "Error: use an age such as 30m, 1h, 1d or 2w." >&2
  exit 2
fi
exec python3 "$SCRIPT_DIR/git-amend-date.py" --date "$1" "$commit"
