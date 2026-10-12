# Brief template

Copy the shape, not the placeholder wording. Omit sections that would be empty;
add the execution sections only once work starts. Write in the repository's
language; front matter keys and status values stay in English.

```markdown
---
title: Compact profile header
status: planned
summary: Show identity, bio and counts at the top of profile pages.
kind: feature
depends_on: [thread-expansion]
---

# Compact profile header

[Roadmap](../ROADMAP.md)

## Outcome

What will be true for the user once this is done, in observable terms.

## Context

Current behaviour and why it falls short. Pointers to the code and maintained
documents an implementer must read, as repository-relative paths, for example
`src/features/ProfileView.vue`. Refresh these when work starts.

## Scope

- In scope: ...
- Not in scope: ... (only exclusions a reader would otherwise assume are
  included; omit this line when there are none)

Constraints the implementation must respect: compatibility, state, failure
behaviour, performance or cost limits.

## Decisions

- 2026-10-07: Reuse the existing profile request; no new endpoint. (agreed)
- 2026-10-07: Show counts as snapshots, not live values. (proposed)

## Open questions

- Which external website field is authoritative?

## Acceptance

- [ ] Observable check, phrased so someone can verify it without guessing.
- [ ] Includes edge cases that matter: missing data, failure, old state.

## Progress

- 2026-10-09: Checkpoint reached, or what changed the plan and why.

## Surprises

- What turned out differently from the brief and how it was handled.

## Evidence

Acceptance results: each criterion passed, failed or untested, with the
command, test or observation behind it. Outstanding owner decisions or checks.
On completion: a dated acceptance line and links to the maintained docs now
describing the behaviour.
```

Use ISO dates (`YYYY-MM-DD`): they sort and compare without ambiguity.

A routine has no Acceptance or Progress; it describes what doing it involves
and keeps a log:

```markdown
---
title: Goal review
status: ready
summary: Review goals and adjust next steps.
kind: routine
every: 2w
last_done: 2026-09-28
---

# Goal review

[Roadmap](../ROADMAP.md)

## Outcome

What each run produces, and what it checks.

## Steps

1. ...

## Log

- 2026-09-28: Done; two goals adjusted, follow-up captured as an idea.
```

An idea brief can be as short as front matter, the backlink and an Outcome
paragraph. A ready brief needs enough of the rest that an agent with no access
to the conversation could implement it and know when it is finished.
