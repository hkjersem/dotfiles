# prune branches

Run as `bash ~/.dotfiles/scripts/prune-branches.sh [options] [path]`. Full
rules: `~/.dotfiles/docs/prune-branches.md`.

It deletes local branches whose upstream branch was deleted on the remote,
once their commits are confirmed on a remote. It first removes each such
branch's clean linked worktree, wherever it is on disk, including outside the
repository. Never-pushed branches and repositories without a remote are never
changed.

## Scope

`path` defaults to the current directory. Inside a repository or worktree
only that repository is checked; elsewhere it searches for repositories
recursively (`--depth`, default 4). Outside a repository, confirm that the
user wants every repository under that directory before running.

## Preview, then apply

Every run, including a preview, fetches with `git fetch --prune` (updating
remote-tracking refs) and recomputes its plan, so a later run can delete more
than an earlier one showed.

1. Preview with `--apply < /dev/null`. It prints the plan it would apply,
   then aborts at the confirmation because there is no answer ("Aborted: no
   answer."), deleting nothing. For a look without fetching, run with
   `--no-fetch` instead (read-only, from the last fetch), but do not use that
   output for approval: `--no-fetch` cannot be combined with `--apply`.
2. Show the user the repositories, the branches it would delete, and the
   worktree paths it would remove, and ask for approval.
3. Immediately before applying, repeat step 1. Run the same command with
   `--apply --yes` only when its plan matches the approved one; otherwise show
   the new plan and ask again. Never run `--apply --yes` without this check.

Add `--include-merged` only when the user asks to also delete merged branches
whose remote branch still exists.

## Report

Deleted branches are logged in each repository's `.git/prune-branches.log`;
one can be restored with `git branch <name> <commit>`. Exit status 1 means a
fetch or deletion failed: say which.
