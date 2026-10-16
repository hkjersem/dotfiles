---
name: test-plan
description: Decide which tests a feature, fix, or refactor actually needs before implementing it, based on what must not break, how likely it is to break, and the cheapest check that would catch it. Use when asked "what should we test", for a test plan or test strategy, or before implementing a non-trivial feature, fix, or refactor. Not for writing the tests themselves, diagnosing a bug whose cause is still unknown, or reviewing a finished diff.
---

# Test plan

Writing tests is cheap now. Choosing them is not. The goal is the smallest set
of checks that would catch the failures that matter and say what broke, not
the largest number of passing tests.

## 1. Size the change

If the change is trivial, such as a rename, copy text, a comment, or a
mechanical refactor that existing tests already cover, say so, name the
existing checks to run, and stop. Do not plan new tests for it.

## 2. Get the expected behavior from outside the code

Tests that restate the implementation confirm its mistakes. Collect what the
change should do from sources other than the code you are about to write or
change:

- The request, issue, or spec.
- Project documentation, decision records, and API or schema definitions.
- Existing tests in the area, and what they already cover.
- Past bugs: `git log` for fix commits touching the same files, and any
  regression tests they added.
- Callers and consumers of what you are changing.

If the expected behavior is ambiguous and the answer changes what you would
test, ask before going further. Otherwise proceed and state your assumptions.

## 3. List what must not break

Name the concrete risks, not categories. Look for:

- **Invariants**: rules that must always hold, such as "never writes outside
  the target directory" or "totals always match the line items".
- **Boundaries**: APIs, stored data and its older versions, file formats,
  events, configuration, and components other code depends on.
- **State changes**: ordering, retries, partial failures, concurrency, and
  what happens when a step runs twice.
- **Trust boundaries**: untrusted input, authorization, and output escaping.
- **User-facing behavior**: critical user flows, and accessibility for UI,
  such as keyboard use, accessible names, and focus.

Rank each by how costly a failure is and how likely the change makes it. Keep
the top few. A long list means nothing was prioritized.

## 4. Choose the cheapest check that points at the cause

For each kept risk, pick the lowest level that reliably catches it and whose
failure says what broke:

| Level | Use for |
|---|---|
| Type checks, linting, schema validation | Shape and contract mistakes the tooling can reject |
| Unit | Pure logic and invariants with clear inputs and outputs |
| Property-based | Invariants over a wide input space, such as parsing and round-trips |
| Integration or contract | Boundaries: storage, serialization, APIs, shared components |
| End-to-end | A few critical user flows across the whole system |
| Manual or usability | What automation cannot judge, such as whether users understand it |

Prefer extending an existing test over adding a new file. An end-to-end test
detects that something is wrong but often not what, so do not use it in place
of a boundary test. Note any check that is slow, flaky, or depends on an
external service, and whether it should block merging.

## 5. Cut

Drop planned tests that duplicate existing coverage, only restate the
implementation, or guard against failures that would be cheap and obvious.
List existing tests the change makes redundant or wrong as candidates for
removal; do not delete them without asking.

## Plan

Present the plan before implementing:

- **Risks covered**: for each, the expected behavior and its source, the check,
  its level, the public interface it tests through, and whether it is new,
  extended, or existing.
- **Not tested**: risks you chose not to cover, and why.
- **Assumptions**: anything you inferred rather than found.

The approved plan settles which interfaces to test through, so do not start a
second planning round. If a test-driven development skill is available, use it
for the implementation. Either way, write each new test before its
implementation, see it fail for the intended reason, then make it pass, one
behavior at a time. Breaking the behavior temporarily can confirm an existing
or extended test goes red, but does not replace writing new tests first.

## When done

Run every check in the plan and report what each one verified, what failed and
why, and which risks remain unverified. Never change a test's expected
behavior to make it pass unless the plan's source for that behavior was wrong;
say so when that happens.
