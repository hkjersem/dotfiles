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
first, and to at most three per Codex five-hour usage window. They are queued together, so
the app takes them within seconds of each other. When a used-up limit resets
before the next check, that check waits and sends a minute after the reset,
as `--reset` does, instead of at the next check. Each chat is resumed once per
limit stop. If its reply hits the limit again, it is resumed after the next
reset.

The cap follows Codex's reported five-hour reset: all three slots become
available together when it resets, regardless of when earlier chats were
resumed. Codex starts its window when the app takes the first message, a
little after the check records the send, so resumes up to 15 minutes before
the reported start still count toward it. There is no separate cooldown per
chat or send. If the limits cannot
be read, the check reports the error and sends nothing.

Each usage window holds up to three chats of one tier. Unmarked chats come
first, then chats starting with `!`, then `!!`, and so on. A lower tier finishes
before the next tier starts its own window, even if it takes several windows
to resume all the lower-tier chats. Different tiers never share a window.
Newly stopped chats of the running window's tier can join it while it has room.

Once it has planned the windows, the check sets each waiting chat's marks to
one `!` per window it waits, the one it goes in included, following the plan
`--status` shows. A running chat has no marks, and a chat the next window takes
has one. With the running window full and `Fix` and `!Fix` waiting, they
become `!Fix` and `!!Fix`; when the next window resumes `Fix`, the other
becomes `!Fix`. This changes the title, not its retained tier: changing marks
automatically never lets tiers merge. A chat can't join the window that passed
it by; this is recorded with the resumes. Marks never hold a chat back when no
lower tier is waiting. When it is resumed, its marks are removed and its next
stop starts a fresh tier. A chat that waited at the last check but no longer
stops at a limit, as when you continued it, also loses its marks; a chat you
mark while it runs keeps them for its next stop.

Adding or removing a leading `!` by hand moves a waiting chat as many tiers
later or earlier at the next check, and removing them all makes it unmarked;
the check then sets its marks to the windows it waits again. Other title edits
leave the tier unchanged. The app shows a title the check changed after it
restarts. Pause the check with `--recurring off` to keep a daytime window free.

With `--morning`, a waiting chat the night holds back past a window that would
otherwise take it also gets a `~`, as in `~!Fix`. A `~` never changes
scheduling. The check records which chats it added `~` to and removes only
those, once the chat is no longer held, so a `~` you write stays and is skipped
when counting `!`; if you remove one the check added, it adds it back.

Records from before tiers were retained can recover only the last removed `!`.
The check adds that to the remaining marks when first assigning their tier.

Add `--morning 09.00` to plan nights around that time. From ten hours before
it, at 23.00 here, a single waiting chat waits for a window from five hours
before it, at 04.00. Two or more are split: an early window takes the smaller
half, or only the lowest tier when tiers differ, and takes no later chats. The
rest wait for the 04.00 window. From an hour after that, at 05.00, chats wait
for the morning, so a new window is free then.

Only chats updated in the last eight days are checked. A chat is left alone
after 24 uninterrupted, observed hours in which quota and a scheduling slot
were available but the chat had no activity. Its stop date alone does not
make it too old. Messages, transcript changes, running turns and title or
priority changes reset the clock, as do quota exhaustion and waiting for
capacity, a later tier or a morning window. A rejected queue attempt is not
chat activity; a sent message is.

The clock is stored with the recurring state. Existing records start fresh.
A check missed by more than one minute beyond the configured interval,
an unreadable observation or reconfiguring recurring resumes resets the
clock rather than counting unobserved time. There is no monitor while
recurring resumes are off; enabling them again starts fresh. Checks sample
quota and activity, so transitions between checks cannot be timed exactly.
A chat whose latest reply stopped with a temporary error gets `continue` at the
next check once no limit is used up: the model at capacity ("Selected model is
at capacity", `server_overloaded`), a rate limit, a failed or lost connection,
too many failed attempts or a server error. Codex has already retried these
itself. The chat skips the windows, tiers and the three-chat cap, and is retried
at each check after another such stop, until it has failed for 24 hours. A
server error can recur for one chat, so it is retried at most three times in a
row. `--status` lists these chats and their error in their own section.
Running chats and chats whose latest reply completed, was cancelled or failed
for another reason are not resumed. The check runs alongside a scheduled
resume, opens the app if it isn't running, and logs only what it sends or
renames, when it waits for a reset, and any errors. `--status` shows
when it last checked and lists the chats stopped by a
usage limit: whether each continues at the next check, when it is planned to
continue, or is left alone as `24 hours unattended`. While a limit is used
up, nothing continues before a minute after it resets. Later windows are
estimates of five hours from their first resume, adjusted to the live reset
at each check; later usage-limit exhaustion is not foreseen. A chat the app hasn't titled yet shows as
`untitled (folder, start of its ID)`. `--recurring off` stops it. If
its record of resumed chats can't be read, it stops sending rather than risk
sending twice, and `--status` and the log say which file to fix or delete.

## Choose a chat

Without a choice, the message goes to the chat on this Mac with the latest
activity, chosen when it is sent. Add `--chat` to `--time`, `--reset` or
`--now` to pick one by id, id prefix or name; the starts of the name's words in
any order also work, such as `explore pwa`. `--list [text]` shows your recent
chats on this Mac with their ids and last activity, including a new chat whose
first turn a usage limit stopped. When several chats match,
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
the Mac to sleep, unless [agent-awake](agent-awake.md) is installed: then the
job asks it to wake the Mac from 90 seconds before the scheduled time, also with the
lid closed, and keep it awake until the message is sent. The job checks the
clock every 30 seconds, so it runs as soon as the Mac wakes. A run that starts
late says so in the log. The recurring check doesn't prevent sleep or request
wakes. Logs: `~/Library/Logs/codex-resume.log`. Requirements and implementation notes
are in [`scripts/codex-resume.py`](../scripts/codex-resume.py).
