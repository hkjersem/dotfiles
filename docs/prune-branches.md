# Prune stale branches

[README](../README.md#prune-stale-branches)

The command shows its plan and asks `[y/N]` before deleting. Enter, Escape,
no, or closed input cancels. Use `--dry-run` to preview without prompting;
`--yes` skips confirmation and cannot be combined with `--dry-run`.
Previews still fetch and update Git metadata unless `--no-fetch` is added.
`--no-fetch` requires `--dry-run`; deletion always needs fresh remote data.

## What gets deleted

A branch is deleted only when its upstream was its own same-named remote
branch, that branch is gone after `git fetch --prune`, and its commits are
still on the remote: a GitHub pull request or GitLab merge request head,
contained in a branch the remote still advertises, or rebased or
squash-merged into the remote's default branch so that merging it would
change nothing.

With `--include-merged`, a branch whose same-named remote branch still exists
is also deleted when its commit is already in the remote's default branch
after the fetch, as after a merge-commit or fast-forward pull request. Only
the local branch (and its clean worktree) is removed; the remote branch is
kept. Squash-merged or rebased branches whose remote branch still exists are
not detected this way.

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

Detached worktrees are report-only by default; clean ones whose commit is already on a
remote are reported as looking stale, together with never-pushed or untracked
branches older than `--stale-after` (default `90d`), and branches already merged
into the default branch whose remote branch still exists (pass
`--include-merged` or delete the remote branch to prune them). A branch
without an upstream that exists on a remote under the same name is shown as
not tracking it, with how far it is ahead or behind.

### Opt-in detached worktree removal

`--include-detached` also plans removal of detached linked worktrees.
It fetches all configured remotes, even if no local branch tracks them.
HEAD must be contained in a remote-tracking branch matching the branch
and commit advertised after a successful fetch; patch equivalence alone
does not qualify. Unconfirmed commits are kept. As elsewhere, `--no-fetch`
is allowed only for a preview, based on existing refs.

Main and invoking worktrees are never removed. Locked, missing, dirty
worktrees, non-regenerable ignored files, hidden index flags, worktree-only
refs and commits held only in HEAD history all block removal. Unfinished
rebases, bisects, merges, cherry-picks and reverts also block removal.
Identity, detached HEAD, remote evidence and local safeguards are rechecked
before `git worktree remove`, without `--force`. No local branch is deleted
for this action.

Stop agent sessions before removal or lock worktrees you want preserved:
clean contents and remote backup do not prove a worktree is inactive.

### Optional retention window

`--older-than 7d` applies to eligible normal branches, `--include-merged`
branches and `--include-detached` worktrees. It requires an older tip
**committer** timestamp and, when removing a worktree, older modification
times for its root and all descendants except `.git` metadata. Supported
units are `m`, `h`, `d`, `w`. It reuses `wipe_clean`'s modification scanner
and does not follow symlink destinations. Unreadable metadata retains the
candidate with a reason; ages are rechecked immediately before removal.
Scanning large worktrees costs time. Filesystem scans and removal are not
atomic; stop writers first. This guard only retains more candidates—it
never makes an otherwise unsafe candidate eligible. Without the flag,
existing eligibility is unchanged.

## Records and recovery

Each fetching run records upstream commits in `.git/prune-branches-upstreams`
so later runs can tell a branch that was pushed and then deleted unmerged
from one with unpushed commits. The record only explains a review entry; it
never justifies a deletion. Records are tied to the remote URL last fetched
(`.git/prune-branches-remotes`), so they say nothing after the remote is
repointed.

Deleted branches are logged in `.git/prune-branches.log`; restore one with
`git branch <name> <commit>` until Git garbage-collects the commits.
Detached removals are logged there as `worktree:<path>`, with their HEAD
commit. Recreate one with `git worktree add --detach <path> <commit>`;
ignored generated output is not backed up.
