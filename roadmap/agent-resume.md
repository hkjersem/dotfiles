---
title: agent-resume for Codex and Claude Code
status: idea
summary: One agent-resume command that resumes stopped Codex and Claude Code sessions, replacing codex-resume.
kind: feature
---

# agent-resume for Codex and Claude Code

[Roadmap](../ROADMAP.md)

## Outcome

`agent-resume` resumes agent sessions that stopped at a usage limit or a
temporary error, on this Mac and the Mac mini: now, at a time, at the next
reset, or with a recurring check. Each supported agent is one backend behind
the same interface; Codex and Claude Code come first. By default it covers
every known agent, and `--agent` narrows it. It replaces `codex-resume`
entirely.

## Context

`scripts/codex-resume.py` (documented in `docs/codex-resume.md`) is today's
Codex-only tool. Its interface and behaviour are the model for
`agent-resume`: scheduling (`--time`, `--reset`), chat targeting (`--chat`,
`--project`), `--recurring` with `!` priority tiers, `--morning` night
planning, the three-chat cap per usage window, temporary-error retries,
`--status`, and the agent-awake wake (`scripts/lib/agent_awake.py`). Its Codex
specifics — the app server queue, Codex session files and usage windows —
become the Codex backend.

Claude Code findings, 2026-11-03, against Claude Code 2.1.312, its docs
(`code.claude.com/docs/en/interactive-mode` "Wait for a usage limit to reset",
`/docs/en/errors`) and the binary:

- Since v2.1.234 an open interactive CLI session waits for a session-limit
  reset and continues on its own. It is on by default (setting
  `autoContinueAtUsageLimit`, "Continue automatically at usage limit" in
  `/config`). The Desktop app's Code tab has a separate "Auto-continue when
  limits reset" checkbox on the session-limit card.
- The CLI retries server errors, overloads, timeouts and dropped connections up
  to 10 times before it ends a turn, so temporary errors matter less than for
  Codex.
- Gaps the native wait leaves:
  1. The wait ends when Claude Code exits, relaunches, or the session moves to
     the background, Desktop or the cloud, and does not restart on resume.
  2. No wait starts by itself for a reset more than 24 hours out (weekly limit)
     or an Opus/Sonnet limit while another model is selected.
  3. It stops after three automatic continuations in a row.
  4. Errors that still end the turn after the retries, such as repeated 529
     overloads.
  5. If the Mac slept more than about 30 minutes and the limit reset during the
     sleep, Claude asks "Press enter to continue" instead of continuing.
     agent-awake releases its hold when the turn stops at the limit, so a
     closed MacBook falls asleep here.
- `claude --bg --resume <id> "continue"` continues a session in the background
  under the same ID, or starts a copy when the session is already open.
  `claude agents --json` lists open sessions (interactive and background), and
  `claude attach <id>` opens a background session in a terminal.

## Scope

- In scope: the `agent-resume` command with Codex and Claude Code backends,
  removing `codex-resume` (script, shell function, completions, docs, tests),
  agent-awake wakes for both, and the one-time steps to move each Mac over.
- Constraints: never continue a session its agent is already waiting on;
  record each resume so nothing is sent twice; Codex behaviour stays as it is
  today.
- Migration: no one-time migration code in the repository. When the work is
  done, give the user commands to paste on each Mac that remove the old
  `codex-resume` LaunchAgents, logs and state, and set up `agent-resume`.

## Decisions

- 2026-11-04: Start with Claude Code as the second agent; others later.
  (agreed)
- 2026-11-04: One `agent-resume` command whose interface matches
  `codex-resume` as far as possible. By default it applies to every known
  agent; `--agent` limits it, possibly to several (for example
  `--agent codex`). (agreed)
- 2026-11-04: The `!` priority tiers, `--morning` night planning and the other
  recurring behaviour apply to every agent. (agreed)
- 2026-11-04: `agent-resume` replaces `codex-resume`; no legacy alias or
  wrapper is kept. (agreed)
- 2026-11-04: Migration is done with commands the user pastes on each Mac, not
  with one-time code committed to the repository. (agreed)
- 2026-11-04: For Claude, leave open sessions to Claude's own wait and fill
  only the gaps above. (proposed)

## Open questions

The owner will answer most of these when work starts.

- How does a Claude transcript in `~/.claude/projects/` mark a usage-limit or
  error stop, and its reset time? Needs a real example once the Claude
  subscription is active again.
- Which Claude surfaces: CLI only, or also the Desktop Code tab and `--bg`
  sessions?
- Which Claude gaps are worth closing? Proposed: 1–3 and 5.
- Is a background session (`--bg`) acceptable for a resumed closed Claude
  session, given you would follow it with `claude attach`, not in the original
  terminal, and a permission prompt would stop it until you attach?
- Does a Claude resume use the session's permission mode, or should it skip
  sessions that would need prompts?
- Does `--agent` take one agent or several (repeated flag or a list)?
- One recurring LaunchAgent for all agents or one per agent; where state and
  logs live.
- How the three-chat cap and usage windows map onto Claude's limits, which
  differ from Codex's.
