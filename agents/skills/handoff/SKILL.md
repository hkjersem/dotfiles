---
name: handoff
description: Create a handoff for a fresh agent or session rollover when explicitly requested. Not for reading or continuing from an existing handoff.
---

# Session handoff

Write a handoff that lets a fresh agent understand the actual state of the work
without the previous conversation. Honor any next-session focus the user supplies.

Favor concise, information-dense prose, but let useful continuation context
determine the length. Completeness and clarity matter more than brevity.
Remove repetition, not necessary details; neither pad nor truncate the handoff
to meet a line or word count.
Give a blocker or decision its full explanation once; status summaries and the
final disposition can refer back briefly instead of repeating the evidence and
permission boundaries.

## File handling

Write `handoff-<description>.md` at the repository root, using a short descriptive
kebab-case name. Outside a Git repository, use the current working directory.

Inspect an existing destination before writing. Replace it only when it belongs
to the same task and checkout and the user requested replacement or it is clearly
this session's own handoff being updated. Otherwise, choose a distinct filename.
Leave unrelated files and existing symlink destinations untouched.

Treat handoffs as temporary session artifacts: do not commit or push them.
Keep them unignored so agent file pickers can discover them.

## Information to preserve

Use clear headings suited to the work; omit empty sections.

| Area | Relevant information |
|---|---|
| Identity | Generation time with timezone, repository root, working directory, branch or detached HEAD, commit, and worktree/base branch when applicable. Mark unavailable metadata rather than inventing it. |
| Goal and state | Requested outcome, next-session focus, what is implemented, and completed, active, pending, or blocked work. Distinguish actual state from intended results. |
| Changes | Attributable direct or delegated model edits for the task being handed off: created, modified, renamed, or deleted files, their purpose, and current state. |
| Decisions and constraints | Decisions already made, their necessary rationale, approval boundaries, unresolved questions, and risks that affect continuation. |
| Evidence | Observed verification outcomes with supporting commands or artifacts, failed attempts, and what remains unverified. Capture existing results rather than rerunning task work merely to populate the handoff. |
| References | Authoritative specifications, code, plans, issues, diffs, or other material needed to continue. |

### Attribution and task scope

The main change list contains attributable edits for the task being handed off,
including work delegated to subagents for that task. Exclude unrelated edits,
even if this agent or one of its subagents made them.
Do not include a repository-wide dirty-file inventory or infer ownership from it.
Git status, diffs, timestamps, and commit history alone do not establish authorship.

- **Direct:** visible tool activity shows this agent made the change.
- **Delegated:** a task-specific subagent report identifies the files it changed.
  Include those edits in the main list with the subagent's provenance and the
  available supporting report or artifacts. Individual patch calls need not be
  visible in the parent session. Distinguish reported results from independently
  observed results; a plan to edit files is not evidence that edits occurred.
- **Uncertain:** available activity or reports do not reliably establish the
  edits or their attribution.
- **Unknown:** attribution is unavailable, including after context loss.

Delegated work is not external merely because the parent did not edit it.
Vague or conflicting subagent claims remain uncertain rather than being promoted
to completed work.

Uncertain, unknown, or user-owned edits belong in a separate "External changes
affecting this task" section only when they have a concrete effect on safe
continuation. Explain that effect, such as a conflicting edit in the next target
file or a blocker. Omit unrelated changes entirely. If no edits are attributable,
say so rather than filling the main list with uncertain claims.

Mark jointly changed files as co-edited, identifying attributable task
contributions without claiming ownership of other edits. Preserve old and new
paths for renames, and the former path and relevant state for deletions.

### Reference availability

Prefer durable, retrievable references over copying existing material. Consider
whether the next agent can actually access them; a temporary path, session-local
result, or restricted URL is not sufficient by itself.

If access is unavailable or uncertain, include the essential continuation context
in the handoff and identify the missing source. Preserve observed outcomes and
their provenance without pretending the original artifact remains available.
Redact secrets and sensitive data from both summaries and references.

## Disposition

End with one explicit overall disposition, while retaining individual task states:

| Disposition | What the next agent should do |
|---|---|
| `continue` | Take a concrete, in-scope next action. Identify the relevant file, symbol, or command and any prerequisites. |
| `blocked` | Wait for a named decision, permission, or external dependency. State who or what can unblock the work and what follows afterward. Do not present the blocked operation as already authorized. |
| `complete` | Stop: the requested work is finished. Record any genuine residual limitations without inventing follow-up work. |

Choose `continue` when useful, authorized work remains despite other blocked
tasks. An executable next step is not required for a blocked or completed task.

## Completion and resume

The handoff is ready when it exists at a safe destination and contains enough
accurate, accessible information to continue, wait, or stop without guessing.
Check for missing decisions, unsupported ownership or success claims, and
references that leave essential context unavailable.

Report the written path and provide this resume instruction:

> Read `{handoff path}`. Compare its recorded repository, worktree, branch,
> commit, and relevant working-tree state with the current checkout. Treat the
> timestamp as age information, not a value that must match the new session.
> Reconcile relevant differences before acting; do not switch or reset the
> checkout just to match the handoff. If the task or checkout is unrelated, stop
> and clarify the intended target. Follow the recorded disposition under current
> instructions and permissions: continue with the next action, seek the named
> unblocker, or stop if complete.
