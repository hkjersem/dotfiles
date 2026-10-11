---
name: tidy-diff
description: Tidy pending or recent code changes — clean up, simplify, DRY up, remove leftovers — and, when asked, commit them as logical commits. Use when asked to tidy, clean up, simplify, or DRY up changes, "tidy and commit", or split changes into commits. Not for prose, read-only review, or unrelated repository-wide refactoring.
---

# Tidy diff

Leave the pending change smaller, clearer, and consistent with its codebase,
without changing intended behavior. Act autonomously: decide and apply the
clear improvements yourself, and ask only where a reasonable engineer would
need the author's intent.

## Safety

Never be destructive. These rules override everything below:

- **Snapshot first.** Before any edit, record what is staged, unstaged, and
  untracked. Use that snapshot to restore staging and to verify the result.
- **Never discard work.** Do not run commands that drop worktree or index
  content, such as hard resets, checking out or restoring files over local
  changes, cleaning untracked files, or dropping stashes. Unstaging without
  touching the worktree is allowed when committing requires it.
- **Undo only your own edits**, by editing them back. Never revert a whole
  file when it also holds the author's changes.
- **Do not delete files** you did not create unless they are clearly
  obsolete because of the change; otherwise ask.
- **Restore staging on every exit**, including when a check, hook, or commit
  fails partway through.
- **Verify at the end** against the snapshot: every change outside your tidy
  edits and the commits you created is still present, with its original
  staging.

If an operation could lose work and you cannot make it safe, stop and ask.

## Scope

Review the change, not the repository:

- Staged and unstaged changes, plus untracked files whose role in the change
  is clear from the request, the conversation, or how the code references
  them. Leave other untracked files untouched, and ask about them only if they
  affect completing the change.
- On a feature branch with no pending changes, the branch's commits since it
  diverged from the default branch. Apply tidy edits as new working-tree
  changes; never regroup or rewrite the existing commits, and present any
  regrouping idea as advice.
- An explicit path, commit range, or file list from the user takes precedence.

Within scope, tidy the author's and the agent's edits alike. Excluding an
unrelated edit from a commit never means discarding it: leave it intact in the
worktree.

Read surrounding code as needed to judge the change, but only modify code
outside the diff when the change itself makes that edit necessary, such as
reusing or extracting shared code the change duplicates, or removing code the
change made obsolete.

## What to look for

Prioritize by impact on future readers and maintainers:

1. **Duplication.** Before judging new logic, search the codebase for
   existing implementations of it, not only for named helpers. New code that
   reimplements an existing helper, utility, constant, or type: reuse or
   extend the existing one. Logic repeated across the change, or between the
   change and existing code: extract a shared function in the place the
   codebase keeps similar shared code, and update the existing copies to use
   it. Do this when there are three or more copies, or two substantial ones
   with the same reason to change. Leave coincidental similarity alone; a
   wrong abstraction costs more than repetition.
2. **Leftovers.** Debug output, commented-out code, unused imports, variables,
   parameters, functions, and files, temporary scaffolding, code made dead by
   this change, and accidentally included generated files or credentials.
3. **Unneeded complexity.** Indirection with a single caller and no clear
   purpose, speculative options or configuration, deep nesting that early
   returns would flatten, and hand-written logic the language or an existing
   dependency already provides.
4. **Clarity.** Misleading or vague names, magic values that deserve a name,
   comments that restate the code or are now stale, and missing comments where
   the reason for non-obvious code is not evident. Prefer explicit, readable
   code over compact or clever code; fewer lines is not the goal.
5. **Consistency.** Deviations from the codebase's established patterns,
   naming, error handling, and documented contributor instructions.
6. **Completeness.** Changed behavior without corresponding tests, and
   documentation, examples, or configuration the change made inaccurate.
7. **Scope.** Unrelated changes mixed into the diff.

Do not duplicate other tools: leave formatting to the project's formatter and
linters, and do not turn this into a security audit or a bug hunt. Report a
defect you notice in passing, but do not let it dominate the pass.

## Decide or consult

Apply the change without asking when it is behavior-preserving, local to the
change, and clearly better by the codebase's own standards. Most leftovers,
reuse of an obviously equivalent helper, and stale documentation fall here.
A bounded extraction of shared code motivated by duplication in the change
also counts as local, including updating existing equivalent callers.

These rules govern your own tidy edits. Behavior changes the author already
made are the change itself: keep them, and mention any that look accidental.

Consult the user when any of these holds for an edit you are considering:

- It could alter observable behavior, a public interface, persisted data, or
  a contract other code relies on.
- Several reasonable designs exist and the choice depends on intent or
  direction you cannot infer.
- It would remove something that might be intentional, such as apparently
  unused code that may be called dynamically, externally, or in future work
  the change hints at.
- It requires edits outside the change beyond such an extraction, such as a
  wider redesign, a new dependency between modules, or equivalence you cannot
  establish.
- The commit boundaries are genuinely ambiguous.

Collect these questions and ask them together after the autonomous pass, each
with the context needed to answer it and your recommended option. Do not ask
about anything you can determine by reading the code, history, or tests.

## Commit plan

Whenever the change mixes concerns, propose the fewest logical commits that
keep them apart, even if committing was not requested:

- Each commit does one thing, can be reverted on its own, and leaves the code
  working. Keep an implementation with its tests and documentation.
- Separate structural changes, such as renames, moves, and cleanups with no
  behavior change, from behavior changes, and order them first when they work
  on their own. Cleanup that depends on newly introduced code stays with that
  code's commit. Dependency order wins: never create a commit that only works
  together with a later one.
- Split by file where possible and by hunk only when a file mixes concerns.
- Your own tidy edits belong to the commit whose code they touch; a cleanup of
  pre-existing code gets its own structural commit.

A small, single-purpose change needs no plan; say it fits one commit.

## Committing

Commit only when the request includes it, such as "tidy and commit". The
request authorizes staging and committing the change, not pushing, amending,
or rewriting history. Then, without further confirmation unless a boundary is
ambiguous:

1. Finish the tidy pass and its checks first.
2. For each planned commit, make the index contain exactly that commit: stage
   its explicit paths, and temporarily unstage anything else that is staged,
   including changes the author staged outside the requested scope, without
   touching the worktree, restoring that staging after the commit.
   For a file that mixes concerns, stage selected hunks non-interactively,
   for example by applying a partial patch to the index. Never stage the
   whole worktree blindly. If the index cannot be made exact reliably, ask
   instead of committing extra changes.
3. Review the staged diff for unrelated churn, leftovers, and whitespace
   errors, and run the narrowest checks for what it contains.
4. Write the message in the repository's convention, inferred from its
   contributor instructions and recent history: a specific subject line in
   imperative mood, with a body only when the reason or impact is not obvious.
5. Commit without bypassing hooks. If a hook fails because of your tidy
   edits, fix or revert them and retry. If it fails for a reason that existed
   before, is unrelated, or comes from the environment, stop committing and
   report the blocker.

Leave changes outside the authorized scope uncommitted, and report them.

## Working rules

- When not committing, preserve the author's staging: if a file was fully
  staged before your edit, stage your edits to it; if it had a partial staging
  split, leave your edits unstaged and say so. Do not otherwise stage,
  unstage, or discard changes.
- Keep edits minimal and in the surrounding style. Do not reformat untouched
  code or expand the change with unrelated improvements.
- Before editing, note whether the relevant checks already pass. After
  editing, run the narrowest available checks covering the touched code, such
  as its tests, type checker, and linter. If your edit causes a new failure,
  fix or revert that edit; do not try to fix failures that existed before you
  started, but report them. Report checks you could not run.

## Result

Report briefly:

- Changes applied, grouped by kind, with file references.
- Checks run and their outcomes, including failures that already existed.
- The commit plan, or the commits created with their hashes and subjects.
- Questions needing the user's decision, each with a recommendation.
- Notable observations deliberately left alone, and remaining uncommitted
  changes, if any.

If the change is already tidy and fits one commit, say so in one line.
