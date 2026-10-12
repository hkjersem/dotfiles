---
name: blast-radius
description: Find what a code or configuration change could break outside its own diff, and prove the fact its safety rests on by running real code. Use when asked for the blast radius of a change, "what could this break", "is this safe to merge", or to check a small diff the user does not trust. Read-only analysis; not for tidying, fixing, or general code review.
---

# Blast radius

Find what a change breaks somewhere else before it ships. Listing callers is
the easy part; the job is the breakage a text search does not show.

This is analysis, not editing. Do not change the code under review. Temporary
probe scripts are fine if they live outside the repository or are deleted
before you finish.

## Scope

Review, in order of precedence:

1. A path, commit, range, or pull request the user names.
2. Staged and unstaged changes.
3. On a feature branch with no pending changes, the commits since it diverged
   from the default branch.

### Default branch

The repository may or may not have a remote. Resolve the default branch, called
`<base>` below, as:

- **With a remote:** use the remote the current branch tracks, otherwise
  `origin` or the only remote; if the choice is still unclear, say which one
  you used. Find its default branch from its symbolic `HEAD`
  (`git symbolic-ref refs/remotes/<remote>/HEAD`, or
  `git ls-remote --symref <remote> HEAD`), fetch it, and compare against its
  remote-tracking ref, such as `origin/main`. If the fetch fails, use the
  existing ref and say how old it is, or that its age is unknown. If no usable
  ref exists, report the check as unavailable. Never use a local copy of the
  default branch instead; it can be stale, or checked out in another worktree.
- **Without a remote:** infer the local default branch from the repository,
  such as `main`, and state which branch you used. If ambiguous, ask rather
  than guess.
- **Neither:** if the current branch is the only candidate, for example when
  working directly on the local default branch, skip the upstream step.

## Evidence levels

A convincing writeup reads the same whether or not it is true. For every claim
the change's safety depends on, record how far it got:

1. **Asserted.** You said so. Worth nothing alone.
2. **Cited.** You pointed at a real `file:line` or the dependency's own source.
3. **Reasoned.** You walked the failure path step by step and showed it cannot
   be reached.
4. **Executed.** A script or test calls the real code and fails loudly if the
   claim is wrong.
5. **Reproduced.** You observed it in the running application or tool.

Push each claim as far as is cheap, and say where it stopped.

## Steps

1. **Read the change.** Note what is added, changed, and removed, and what now
   behaves differently, including what the diff does not spell out: new
   defaults, changed ordering, new failure modes, removed side effects.
2. **Check removed code.** For each removed or loosened check, guard, branch,
   or workaround, run `git log -L` or `git blame` on it and read the commit
   that introduced it. Code that fixed a bug or handled an edge case may be
   coming back as a regression.
3. **Find the fact it is safe because of.** Most risky-looking changes are safe
   because of one fact, such as "this function is only called during startup"
   or "every caller already passes a non-empty value". Name it. If it holds,
   most risks clear at once. Spend your time here rather than on a long list of
   possibilities.
4. **Look where search stops.** Follow dependencies that symbols do not show:
   - Configuration, environment variables, feature flags, and defaults.
   - Files read by other programs, languages, or tools: data formats, generated
     output, file names, paths, and links.
   - Serialized shapes: API responses, database columns, cache entries,
     message payloads, and their older stored versions.
   - Timing and lifecycle: startup order, teardown, retries, concurrency, and
     partial earlier runs.
   - The source of called dependencies at their pinned version, including local
     patches, rather than their documentation.
   - Platform and runtime versions the project supports.
5. **Check upstream.** If `<base>` has commits the branch lacks
   (`git rev-list --count HEAD..<base>`), read the ones that touch the changed
   code, its callers, or what it depends on. New callers of changed code and
   changed dependencies break only after merging, and neither side's diff shows
   it. Check for textual conflicts with `git merge-tree --write-tree --name-only
   HEAD <base>` (Git 2.38 or later). If that command is unavailable, report
   files changed on both sides since `git merge-base HEAD <base>` as possible
   conflicts. Either way this covers committed work only, so compare
   uncommitted changes with the upstream commits separately. Do not merge,
   rebase, or check anything out.
6. **Check test coverage.** Identify changed behavior that no test exercises.
   Uncovered changes on a risky path raise that risk.
7. **Rate each risk.** Give how it breaks, where (`file:line`), how likely, and
   how costly. Never invent a caller, an API, or a behavior; a search that finds
   nothing is a result and should be reported as one.
8. **Prove the key fact.** Write the smallest script or test that runs the real
   code and fails if the fact is false. Run it and include the command and
   output. If you cannot run it, mark the fact unproven and say why.

## Report

- **What changed.** Including the non-obvious part.
- **Safe because of.** The key fact, its evidence level, and the proof. Mark
  it unproven if it stopped below "Executed".
- **Risks.** Each with mechanism, location, likelihood, cost, and how to check.
- **Cleared.** What you checked and why it is fine.
- **Before merging.** The cheapest test or reproduction that would catch the
  real failure, and whether it is worth keeping as a permanent test. If the
  branch is behind `<base>` in ways that matter, say so, naming the base
  compared and, if the fetch failed, how old it was.

Keep it short. Leave out sections with nothing in them.
