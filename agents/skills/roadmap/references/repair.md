# Repairing and migrating a roadmap

Use when the owner asks to fix, clean up, tidy or migrate a roadmap. The aim is a
roadmap that is accurate and cheap to read, without losing information or
making decisions that belong to the owner.

## Survey first

Read the index and the documents that link to the roadmap (README, AGENTS.md,
documentation indexes). For briefs, read the status, dependency and date lines
first, and the rest only where they disagree with the index. Run `check` when
the layout is conforming. Note where status is recorded, how many places repeat
it, which items look finished, what has no brief, and which links or code
pointers are stale.

A cleanup request is not a migration request. In a foreign format, apply only
the first group below, keep its own status names and structure, and offer
migration as a proposal.

## Apply directly

These changes are mechanical, verifiable and keep meaning intact.

In any format:

- Broken links and anchors, and code pointers whose new location is certain.
- Disagreement between index and item file, resolved toward whichever the
  roadmap's own rules make authoritative; in this skill's layout that is the
  brief. When a foreign format names no authority, or the two disagree about
  status or acceptance, report the disagreement for the owner instead of
  choosing. Resolved disagreements include index columns that paraphrase the
  brief, such as dependencies or status qualifiers. A fact that appears only in
  the index moves into the brief before the index entry changes; never drop
  it. When the brief is stale, update facts from newer dated records, but treat
  any status change implied by them under the rules below.

In this skill's layout only:

- Status names that record the same owner decision as a lifecycle status, such
  as `in progress` to `active` or `shipped, awaiting review` to `verifying`.
  Translate only what the old status already says; a complete spec alone does
  not make an item `ready`, and finished-looking code does not make it `done`.
- Regenerating the index, restoring a missing backlink, adding a missing
  `summary` taken from the brief's own text.
- Archiving items whose record shows the owner accepted them: a dated
  acceptance line, or a done status the owner asked for. A "complete" status
  with no sign of the owner's approval needs confirmation; list it as a
  proposal.

## Propose first

Anything that removes content or changes what the roadmap commits to:

- Marking items done or dropped without recorded acceptance, including items
  that look shipped from the code. In another format, judge "done" by that
  roadmap's own completion rule (for example "acceptance checks pass") and say,
  per item, what that rule still lacks; ask for owner approval only where the
  rule requires it or the record is unclear.
- Deleting ideas, notes or history; splitting or merging items.
- Changing commitment (`idea` to `planned`), readiness or parking.
- Mapping ambiguous statuses ("parked, likely delete", "partly done"). For
  "implemented" or "shipped", propose `verifying` when checks or acceptance are
  named as outstanding, otherwise `done` for the owner to confirm; leftover
  follow-ups become separate ideas rather than keeping the item open.
- Moving narrative prose, such as a "current direction" section that has become
  a changelog, out of the index; say where each part would go. Usually that is
  the item it describes (its brief, or its archived brief), keeping only the
  current direction in the index.
- Moving files that are not items out of the brief directory: idea lists become
  one-liners under Ideas or `idea` briefs, and design notes go to the
  documentation.
- Relocating the roadmap to `ROADMAP.md` and `roadmap/`.

Present proposals as a short list grouped by decision, with your recommendation
and the evidence for each, so the owner can answer in one reply.

## Migration

Migrate a foreign format only when asked. Do it as its own step after the owner
agrees to the target layout:

1. Create one brief per open item, carrying over every fact and decision. When
   the old record is ambiguous, keep the original wording and add an open
   question rather than guessing. Keep existing item IDs as slug prefixes
   (`m03-wiki`) when commits or documents refer to them; otherwise use
   descriptive slugs. Do not continue a numbering scheme for new items:
   parallel agents would pick the same next number, and numbers suggest a
   ranking. Keep existing dates as written; new entries use ISO.
   Drop "last reviewed" stamps, since version history records that.
2. Finished items with useful decisions or evidence become archived briefs.
   Finished items with nothing worth keeping can be listed once in the commit
   message or a short note instead, if the owner agrees.
3. Turn an old priority order into `depends_on` only where one item genuinely
   requires another. Otherwise drop the ranking; note anything marked "next" or
   "urgent" in the report so the owner can decide whether it still holds.
   Keep existing out-of-scope lists verbatim; they are history. List any
   undecided ones that read like follow-up work in the report so the owner can
   turn them into ideas. Do not make archiving wait on them.
4. Run `init` if needed, then `sync` and `check`. Update every link to the old
   location, including AGENTS.md and README.
5. Find the repository's other planning instructions: agent definitions,
   prompts, AGENTS.md sections and contributor docs that number items, edit
   the index by hand, track status in two places or gate archiving. Propose
   rewriting or retiring each one as part of the migration, and keep the
   project-specific checks they contain (validation, companion changes) by
   moving them to AGENTS.md or the relevant docs. Instructions left in
   conflict with the new layout will keep steering agents the old way.
6. Report what moved where, what was kept verbatim, and what still needs a
   decision.
