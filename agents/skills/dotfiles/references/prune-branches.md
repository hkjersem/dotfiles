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

1. Preview with `--dry-run`. It prints the plan without prompting or deleting.
   For a look without fetching, add `--no-fetch` (read-only, from the last
   fetch), but do not use that output for deletion approval.
2. Show the user the repositories, the branches it would delete, and the
   worktree paths it would remove, and ask for approval.
3. Immediately before applying, repeat step 1. Run the same command with
   without `--dry-run`, adding `--yes`, only when its plan matches the approved
   one; otherwise show the new plan and ask again. Never run `--yes` without
   this check. Plain invocation prompts [y/N]; closed input cancels.

Add `--include-merged` only when the user asks to also delete merged branches
whose remote branch still exists.
Add `--include-detached` only when the user asks to also remove eligible
detached linked worktrees. It fetches all configured remotes and checks
remote reachability, worktree-only history, locks and unfinished operations.
Include those paths explicitly in the approved plan. A removed detached
worktree is logged with its HEAD commit, not as a deleted branch.

`--older-than 7d` is an optional retention guard for all removal candidates:
the tip commit and worktree contents outside `.git` must be older.
It adds metadata scans and is not proof of inactivity. Preserve all requested
selection and retention flags in previews and the approved apply command.

## Report

Deleted branches are logged in each repository's `.git/prune-branches.log`;
one can be restored with `git branch <name> <commit>`. Exit status 1 means a
fetch or deletion failed: say which.
