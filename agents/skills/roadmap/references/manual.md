# Roadmap manual

Full operating rules for a roadmap in this skill's layout. Read SKILL.md first;
it decides whether this file applies.

A roadmap here is a planning record for a repository with one owner and several
coding agents, often without pull requests, CI or a remote. Its job is to let any
agent pick up an item from its brief alone, and to let the owner see what is
committed, in progress and finished without reading every brief.

Two failure modes shape the rules below: documents that drift from each other
and from the code, and documents that grow until the owner stops reading them.
Writing detail is cheap for agents; the owner's review attention and the cost of
stale information are not.

## Layout

| Path | Contents |
|---|---|
| `ROADMAP.md` | Purpose paragraph, short "how this works" header, generated Now / Next / Routines / Later block, hand-written Ideas and Not doing |
| `roadmap/<slug>.md` | One brief per item. Its front matter is the only record of status |
| `roadmap/archive/<slug>.md` | Done and dropped briefs, kept as decision records |

These are defaults. If the repository already keeps its roadmap elsewhere (for
example `docs/roadmap.md` with `docs/roadmap/`), keep that location and pass
`--index`/`--dir` to the script. Use a stable, descriptive slug; it is the item's
identity in links and dependencies, so do not rename it casually. Do not number
new items, even when older ones carry numbers: parallel agents would collide on
the next number.

Shipped behaviour belongs in the repository's maintained documentation, not in a
brief. Decisions that span several items belong in a design document or ADRs that
the briefs link to.

## Briefs

Use [brief-template.md](brief-template.md). Front matter
uses a restricted syntax the script can parse without YAML: one `key: value` per
line, `depends_on` only as `[a, b]`, and single quotes around a value containing
`: ` or ` #` (write a literal `'` as `''`). The script rejects anything else
rather than guessing.

| Key | Meaning |
|---|---|
| `title` | Short descriptive name |
| `status` | One of the statuses below |
| `summary` | One line shown in the index |
| `kind` | `feature` (default), `debt`, `bug`, `investigation` or `routine` |
| `depends_on` | Slugs that must be done first (optional) |
| `blocked` | Why work cannot proceed right now (optional) |
| `when` | Entry condition for an item in Later (optional) |
| `due` | Target date, `YYYY-MM-DD` (optional) |
| `every` | Routines only: interval such as `10d`, `2w` or `1m` |
| `last_done` | Routines only: date it was last done |

Make detail proportional to commitment. An idea needs its problem and why it
might matter. A ready item must let an agent with no access to the conversation
implement it: outcome, current behaviour, scope, code and document pointers,
settled decisions, and observable acceptance criteria. Mark proposals as
proposals; do not turn an open question into an agreed requirement by writing
it confidently.

List a non-goal only when a reader would otherwise assume it is included, or
when the owner has explicitly excluded it; never invent exclusions to fill the
template. An exclusion is a settled boundary, not a backlog: a follow-up worth
doing becomes its own idea or brief, and a rejected direction that might come
back goes in Not doing. Nothing needs reviewing before archiving.

Once work starts, the brief also serves as the execution plan: add Progress,
Surprises and Evidence sections. Record checkpoints, discoveries and decisions
that a later agent would need, not every step taken. A progress log that narrates
routine work becomes a changelog nobody reads.

## Status

| Status | Meaning |
|---|---|
| `idea` | Captured, not committed |
| `planned` | The owner committed to it; decisions are still open |
| `ready` | Fully specified; an agent could implement it without asking anything |
| `active` | Implementation was requested and has started |
| `verifying` | Built; named checks or the owner's acceptance are outstanding |
| `done` | Agreed scope delivered and accepted; archive it |
| `dropped` | Decided against; record why and archive it |

Items may skip statuses, for example `active` straight to `done`. A blocker is
the `blocked` field, not a status.

The generated block groups open items by status alone:

- **Now:** active and verifying.
- **Next:** planned and ready, unordered. Ready items are listed first.
- **Routines:** committed recurring work, with its next date (only when any exist).
- **Later:** ideas, and committed items parked with a `when` condition.

There is deliberately no priority ranking. Among committed items the owner
chooses what to do when, often by what feels right at the moment, and asking
them to rank every addition costs attention without improving decisions. When
one item genuinely must come before another, record that as `depends_on`.
Deferring a committed item means giving it a `when` condition; removing the
condition brings it back to Next.

## Dates and routines

A `due` date records a real deadline or an agreed target, not a guess at when
work might happen; do not add one to make the roadmap look scheduled. Setting
or moving it is the owner's decision.

A routine is recurring work with no end, such as a review cadence or periodic
dependency updates. It has `kind: routine`, `every` and, once done, `last_done`.
Its next date is `due` when set (use that for the first occurrence or a one-off
shift), otherwise `last_done` plus `every`. The interval counts from when it
was actually done, so a late run moves later dates too; monthly steps clamp to
the month's end, so a routine done on the 31st falls due on the 28th in
February and stays on the 28th afterwards. Committed routines get their own
Routines list; an `idea` or a `when` condition pauses one in Later. Routines
never become `verifying` or `done`, and nothing can `depends_on` one; stopping
one means dropping it. A routine can be `blocked` like any item.

Nothing runs on a schedule. The roadmap only shows what is due when someone
runs `ready` or asks what to do next; real scheduling needs cron or similar,
outside the roadmap. The generated block shows dates, not "overdue", so it does
not go stale overnight.

## Who decides

The owner decides commitment and order; agents keep the record accurate.

- **Owner:** committing to an idea (`planned`), declaring an item `ready`,
  parking or unparking it with `when`, accepting work as `done`, dropping items.
- **Agents:** setting `active` when asked to implement, `verifying` when the work
  is built, recording progress, evidence and acceptance results, and keeping
  pointers and links current.

Agents may propose any owner decision, with reasoning, and apply it once the owner
agrees. A roadmap entry, even a ready one, does not authorise implementation; the
owner's request does. An explicit request to implement an item can also settle
its readiness. When the owner has said in advance that an item may close once its
checks pass, record that delegation in the brief and close it on that basis.

`active` reports who is working on what; it is not a lock. Agents in separate
worktrees cannot see each other's edits, so the owner assigns distinct items.
If you find an item already active without being told it was handed to you,
ask before working on it.

## Operations

Run the bundled script from the repository root:
`python3 <this skill's directory>/scripts/roadmap.py <command>`
(the skill's directory is the parent of this `references/` folder).

| Command | Use |
|---|---|
| `init` | Create the index, brief directory and archive. Refuses to overwrite an existing index |
| `sync` | Regenerate the block after changing briefs |
| `check` | Read-only consistency check; each problem includes a fix. `--strict` adds heuristics |
| `ready` | Show what is due, what is in progress and which committed items can start |
| `archive <slug>` | Move a done or dropped brief into the archive, rewrite links to and from it across the repository's Markdown, sync |

`sync` leaves the index unchanged when the brief directory is missing, a brief's
front matter cannot be parsed, or the index markers are damaged.

Create an item only when the owner asks, or when work is being deferred and
should not be lost. Implementing something directly needs no item. If the work
matches an existing item, update that item; otherwise leave the roadmap alone
and at most mention a follow-up worth recording.

- **Add:** write the brief as `idea` unless the owner has said they will do it,
  then sync. Do not ask where it ranks. A thought not yet worth a brief goes
  under Ideas as a one-liner with its entry condition.
- **Start:** when asked to implement, refresh the brief's code pointers against
  the current code, set `active`, sync.
- **Ship:** update the maintained documentation for the new behaviour, then set
  `verifying` with acceptance results in the brief: each criterion passed,
  failed or untested, with evidence, plus what the owner still needs to decide
  or check. Report the same summary to the owner rather than expecting them to
  reread the brief.
- **Finish:** after the owner accepts, set `done`, add a dated acceptance line
  and links to the maintained docs, then `archive`.
- **Drop:** with the owner's agreement, set `dropped`, record the reason, then
  `archive`. If the rejected direction should not come back, add it to Not doing.
- **Do a routine:** when asked, do the work, add a dated line under `## Log`
  with what came out of it, set `last_done`, remove any `due`, sync. A run
  needs no `active`; if a long run was marked active, set it back to its
  previous status. Follow-up work it uncovers becomes separate items, not part
  of the routine.
- **Split or supersede:** when parts can be committed to, timed or finished
  independently, create separate briefs and link them. Mark superseded
  decisions and link their replacement. To defer part of an item, split that
  part into its own brief with a `when` condition (or as an idea) rather than
  leaving a deferred checkbox inside the original.
- **What next:** run `ready` and present what is due first, then the startable
  items briefly, noting committed items whose open decisions block them. Let the
  owner choose; offer a recommendation only when something makes one item
  clearly better to do first, such as an overdue routine or unblocking others.

Run `check` before handing work back. When sync conflicts during a merge,
resolve the briefs, then regenerate; never merge generated lines by hand. If
Python is unavailable, edit the briefs and tell the owner the index needs
regenerating rather than maintaining the generated block by hand.

Without the skill, agents still have the index header and AGENTS.md. Keep both
accurate: the header explains the model in a few lines, and AGENTS.md should
point to it, for example:

```markdown
## Roadmap
Planned work lives in [ROADMAP.md](ROADMAP.md); each item's brief in `roadmap/`
is self-contained. Read the brief before working on an item. Listing an item
does not authorise implementing it.
```

When `check` reports that records disagree with each other or with the code,
fix them following [repair.md](repair.md); the same rules on what to apply
directly and what to propose first apply.
