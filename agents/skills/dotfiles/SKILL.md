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

## Discover commands

For an execution or discovery request, use the shared registry instead of a
separate command catalogue:

```sh
python3 ~/.dotfiles/scripts/dotfiles-help.py --agent-guide
python3 ~/.dotfiles/scripts/dotfiles-help.py --agent-guide <command-or-search-words>
```

This read-only lookup prints supported commands, descriptions, cautions,
direct script invocations and the reference to read first. Match by name or
task. A command appearing in ordinary `dotfiles-help` is not automatically
supported by this skill; use the `--agent-guide` subset. If the flag, script or
reference is unavailable, report it rather than guessing.
For explanation-only questions, or if the user says not to run commands, read the registry in
`~/.dotfiles/scripts/lib/command_help.py` as data and follow its reference
instead of executing even the discovery lookup.

Read the listed reference before the first execution in a conversation, then
use the script's `--help` for current flags. Discovery never authorises execution
or expands the user's requested scope. Read the
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
- When the reference requires approval before a change, end with a direct
  question the user can answer, such as "Delete these 2 branches and 1
  worktree?". Saying that approval is required is not asking for it.
- When explaining a command that deletes or changes things, also say what has
  to happen before it applies them, as its reference describes.
