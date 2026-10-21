# codex-resume

Run as `python3 ~/.dotfiles/scripts/codex-resume.py`. It needs macOS and the
Codex desktop app, and acts on this Mac only. Full details:
`~/.dotfiles/docs/codex-resume.md`.

## Choose the chat

The user must name the chat or project. Never infer it from the current
directory or from which chat was active last.

- `--list [QUERY]` shows recent chats with their id, name and project.
- `--chat QUERY` takes an id, id prefix or name; `--project QUERY` takes the
  latest chat in a project folder, chosen when the message is sent.
- If several chats match, the command lists them and sends nothing. Show the
  list and ask which one; never pick one yourself.
- If the user named neither, run `--list` and ask. Without `--chat` or
  `--project` the command uses the chat with the latest activity, so do not
  omit both.

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
`--status`; if one is already scheduled, tell the user what it is and ask
before replacing it. `--cancel` removes it, and `--limits` shows the usage
limits.

`--recurring` is not tied to a chat or project: it resumes every chat on this
Mac that a usage limit stopped, in any project, until `--recurring off`. Say
so before enabling it. It runs alongside a scheduled resume, and `--cancel`
leaves it alone.

## Report

For a scheduled run, give the date and time the command reported and say the
result is logged to `~/Library/Logs/codex-resume.log`. If the output says to
check the chat before retrying, do not run it again: the message may already
have been sent.
