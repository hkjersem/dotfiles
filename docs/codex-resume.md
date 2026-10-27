# Resume Codex later

[README](../README.md)

## Schedule

`codex-resume --time 15.03` sends `continue` to a Codex chat on this Mac three
seconds after the next occurrence of that local time. Accepts `15:03`, `15.03`,
or `1503`. `--now` sends right away; use `--cancel` or `--help` for the rest.
`--status` shows three sections: the recurring check, the chats stopped by a
usage limit, and the scheduled resume. Headings are bold in a terminal unless
`NO_COLOR` is set.

`codex-resume --reset` waits for the used-up 5-hour or weekly limit instead,
or the later one if both are, and sends a minute after it resets.
`--limits` shows both limits, read live through the Codex CLI.

Add a number of minutes, at least 5, such as `--reset 30`, to also read the
limits that often while waiting, for limits OpenAI resets early. It sends once
no limit is used up and the limits that were used up show 0% used. If they
already show use, someone started on the new allowance, for example from
another device, so it stops without sending. At the reset time it sends unless
that is the case, also when the limits cannot be read. Each check is logged
with the limits it read. Like `--reset`, it refuses to schedule when no limit
is used up.

Add `--start-window` to `--time` or `--now` to send only when no 5-hour window
is running, so the message starts a new one, for example at midnight to get an
earlier reset. When the run starts, it checks the limits; if a window is
already running or a limit is used up, it logs why and sends nothing.

## Resume chats stopped by a usage limit

`codex-resume --recurring 15` checks every 15 minutes, at least 5, for chats on
this Mac whose last replies stopped at a usage limit. Once no limit is used up,
it sends `continue` through the Codex queue to up to three of them, newest
first, and to at most three in any five hours. They are queued together, so
the app takes them within seconds of each other. When a used-up limit resets
before the next check, that check waits and sends a minute after the reset,
as `--reset` does, instead of at the next check. Each chat is resumed once per
limit stop. If its reply hits the limit again, it is resumed after the next
reset.

Each 5-hour window holds up to three chats of one tier. Unmarked chats come
first. Start a chat's title with `!` to stagger it to the next window, `!!` to
the window after that, and so on. Marks never hold a chat back when no lower
tier is waiting, and a chat of the window's tier or lower that stops meanwhile
joins it while it has room. When a marked chat is resumed, its marks are
removed. The app shows a title changed this way after it restarts. Pause the
check with `--recurring off` to keep a daytime window free.

Add `--morning 09.00` to plan nights around that time. From ten hours before
it, at 23.00 here, a single waiting chat waits for a window from five hours
before it, at 04.00. Two or more are split: an early window takes the smaller
half, or only the lowest tier when tiers differ, and takes no later chats. The
rest wait for the 04.00 window. From an hour after that, at 05.00, chats wait
for the morning, so a new window is free then.

Only chats updated in the last eight days are checked. A chat stopped more than
24 hours before the most recently stopped chat is skipped: an earlier limit
stopped it, and it was likely left on purpose. The check runs alongside a
scheduled resume, opens the app if it isn't running, and logs only what it sends, when it waits for a reset, and any
errors. `--status` shows when it last checked and lists the chats stopped by a
usage limit: whether each continues at the next check, when it is planned to
continue if the limits allow it then, or is left alone as an older limit
stopped it. A chat the app hasn't titled yet shows as
`untitled (folder, start of its ID)`. `--recurring off` stops it. If
its record of resumed chats can't be read, it stops sending rather than risk
sending twice, and `--status` and the log say which file to fix or delete.

## Choose a chat

Without a choice, the message goes to the chat on this Mac with the latest
activity, chosen when it is sent. Add `--chat` to `--time`, `--reset` or
`--now` to pick one by id, id prefix or name; the starts of the name's words in
any order also work, such as `explore pwa`. `--list [text]` shows your recent
chats on this Mac with their ids and last activity. When several chats match,
they are listed instead of guessing. Chats on another Mac that the app reaches
through remote control are left out: run `codex-resume` on that Mac instead.

Use `--project name` instead of `--chat` for the most recently active chat in
a project, matched by its folder name the same way, such as `local-relay`. A
scheduled run picks that chat when it starts, so a chat started after
scheduling is used.

## Delivery

Every message goes through the Codex queue, through the CLI bundled with the
app. It works while the screen is locked, needs no Accessibility permission,
and leaves any draft alone. The app sends the message within about ten seconds
once the chat is not busy and loaded. If the app isn't running, it is opened in
the background. The app doesn't send to a chat it hasn't loaded, so when it
hasn't taken the message within 30 seconds, the chat is opened in the
background, also on a locked screen. That switches the app to the chat, so it
is skipped while you may be using the app: Codex is the frontmost app, the
screen is unlocked, and you've used the Mac within the last five minutes. It
is tried again every few seconds, so it still opens once you step away. If
the app hasn't taken the message within two minutes, the message stays queued
and the app sends it once you open the chat; the command doesn't wait for that.

If Codex is still replying in that chat, `continue` is dropped, while a
`--message` is queued to follow the reply. When it's unclear whether a message
was queued or taken, the run says to check the chat before retrying. After the
app takes it, the log says whether the reply finished or ended with an error,
such as a usage limit.

Add `--message "text"` to `--time`, `--reset` or `--now` to send that text
instead. It must be one line and not start with `/`.

## Requirements and logs

A scheduled resume prevents idle sleep while it waits, through `caffeinate -i`;
the display still turns off and the screen locks. macOS shows a "Background
Items Added" notification for each scheduled job. Closing the lid still puts
the Mac to sleep; a run that starts late says so in the log. The recurring
check doesn't prevent sleep. Logs: `~/Library/Logs/codex-resume.log`. Requirements and implementation notes
are in [`scripts/codex-resume.py`](../scripts/codex-resume.py).
