---
name: dotfiles-codex-resume
description: Send 'continue' or a message to a Codex desktop chat now, at a set time, or when a usage limit resets, using the dotfiles codex-resume command. Only when the user asks for it by name.
disable-model-invocation: true
---

# Codex resume

Run the command as `python3 ~/.dotfiles/scripts/codex-resume.py` (the
interactive shell's `codex-resume` function is not loaded in agent shells). It
needs macOS and the Codex desktop app. `--help` lists every option; the
details are in `~/.dotfiles/docs/codex-resume.md`.

## Choose the chat

- `--list [QUERY]` shows recent chats with their id, name and project.
- `--chat QUERY` takes an id, id prefix or name; `--project QUERY` takes the
  latest chat in a project folder, chosen when the message is sent.
- If several chats match, the command lists them and sends nothing. Show the
  list and ask which one; never pick one yourself.
- Without `--chat` or `--project`, the message goes to whichever chat is open
  in the app, which needs an unlocked screen. Prefer naming the chat.

## Send

| Request | Options |
|---------|---------|
| Now | `--now` |
| At a local time | `--time HH:MM` |
| When the used-up limit resets | `--reset`, or `--reset 30` to also check every 30 minutes and send early |
| Only to start a new 5-hour window | add `--start-window` to `--now` or `--time` |
| Keep resuming chats a usage limit stopped | `--recurring MINUTES` (at least 5), or `--recurring off` |
| A message other than `continue` | add `--message "TEXT"` (one line, not starting with `/`) |

Only one resume can be scheduled. Before `--time` or `--reset`, run
`--status`; if one is already scheduled, tell the user and ask before
replacing it. `--cancel` removes it, and `--limits` shows the usage limits.
The recurring check is separate: it runs alongside a scheduled resume, and
`--cancel` leaves it alone.

## Report

Quote the command's output. For a scheduled run, give the time it reported
and say the result is logged to `~/Library/Logs/codex-resume.log`. If the
output says to check the chat before retrying, do not run it again: the
message may already have been sent.
