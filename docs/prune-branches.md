# Prune stale branches

[README](../README.md#prune-stale-branches)

## What gets deleted

A branch is deleted only when its upstream was its own same-named remote
branch, that branch is gone after `git fetch --prune`, and its commits are
still on the remote: a GitHub pull request or GitLab merge request head,
contained in a branch the remote still advertises, or rebased or
squash-merged into the remote's default branch so that merging it would
change nothing.

Anything else is listed for review, including branches that were pushed but
then deleted on the remote without being merged, since your copy may be the
last one. Branches that were never pushed, repositories without a remote,
protected names (`main`, `master`, `develop`, `trunk`, and each remote's
default branch), and branches on remotes that could not be fetched are never
deleted.

## Worktrees

Worktrees are found through each repository's `git worktree list`, so
worktrees created elsewhere, for example by agent tools, are included. A
branch's linked worktree is removed first, without `--force`, and only when it
has no uncommitted changes, no ignored files besides generated artifacts
such as an ignored `node_modules/` folder, and no commits or refs that only
that worktree holds. Otherwise it is listed, as are locked, missing, and
checked-out worktrees, and branches with a rebase or bisect in progress.

Detached worktrees are never removed; clean ones whose commit is already on a
remote are reported as looking stale, together with never-pushed or untracked
branches older than `--stale-days` (default 90). A branch without an upstream
that exists on a remote under the same name is shown as not tracking it, with
how far it is ahead or behind.

## Records and recovery

Each fetching run records upstream commits in `.git/prune-branches-upstreams`
so later runs can tell a branch that was pushed and then deleted unmerged
from one with unpushed commits. The record only explains a review entry; it
never justifies a deletion. Records are tied to the remote URL last fetched
(`.git/prune-branches-remotes`), so they say nothing after the remote is
repointed.

Deleted branches are logged in `.git/prune-branches.log`; restore one with
`git branch <name> <commit>` until Git garbage-collects the commits.
