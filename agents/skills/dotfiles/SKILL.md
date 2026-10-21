---
name: dotfiles
description: Run the user's dotfiles commands from any repository. Use only when the user says "dotfiles" and asks for a command or task, for example "run dotfiles prune branches".
---

# Dotfiles commands

The commands live in `~/.dotfiles`. The interactive shell's aliases and
functions are not loaded in agent shells, so run the scripts directly.

## When to run

Saying "dotfiles" only selects this skill. Run a command only when the user
directly asks you to run it now. Mentions in discussion, quotes, code review,
or questions about how a command works are not requests: answer them without
running anything. If it is unclear which command, which options, or which
repository or chat is meant, ask before running.

## Commands

| Command | Request | Run | Read first |
|---------|---------|-----|------------|
| `codex-resume` | Send `continue` or a message to a Codex desktop chat, now, at a time, or when a usage limit resets | `python3 ~/.dotfiles/scripts/codex-resume.py` | [references/codex-resume.md](references/codex-resume.md) |
| `prune_branches` | Delete local branches, and their worktrees, whose remote branch is gone | `bash ~/.dotfiles/scripts/prune-branches.sh` | [references/prune-branches.md](references/prune-branches.md) |
| `audit-repo` | Triage report for a repository: Git activity, tests, build, docs, packages | `bash ~/.dotfiles/scripts/audit-repo.sh` | [references/audit-repo.md](references/audit-repo.md) |
| `pmu` | Update npm, pnpm or bun dependencies, skipping releases newer than a cooldown | `bash ~/.dotfiles/scripts/package-manager/run.sh` | [references/package-managers.md](references/package-managers.md) |
| `pma` | Audit dependencies for vulnerabilities, and fix them on request | `bash ~/.dotfiles/scripts/package-manager/audit.sh` | [references/package-managers.md](references/package-managers.md) |

Match the request by command name or by task. Other dotfiles commands are not
covered: say so rather than guessing a script. Read the reference before the
first run in a conversation; each script also takes `--help`. Read the
matching doc in `~/.dotfiles/docs/` only when explaining how a command works,
interpreting unexpected output, or when the reference does not cover the case.

## Rules

- Never edit files in `~/.dotfiles` while working in another repository.
- If `~/.dotfiles` or a script is missing, say the dotfiles are not installed
  on this machine. If it exists but access is denied, or a sandbox blocks the
  command, its network access, or its writes, stop: report the block and give
  the exact command for the user to run. Never work around it.
- Quote the command's output. After a failure, say what it changed and what
  it left unchanged, as far as the output shows.
