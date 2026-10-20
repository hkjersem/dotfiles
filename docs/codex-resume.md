# Resume Codex later

[README](../README.md)

## Schedule

`codex-resume --time 15.03` submits the open Codex conversation's draft three
seconds after the next occurrence of that local time, or inserts `continue`
if empty. Accepts `15:03`, `15.03`, or `1503`. Use `--status`, `--cancel`,
`--now`, or `--help`; `--inspect` reads the draft and `--check` checks controls.
If the input field isn't recognised, sends only Enter to Codex's existing focus,
without typing text or retrying afterward. Scheduled runs that find no input
field at all retry first and send that Enter only on the last attempt.

`codex-resume --reset` waits for the used-up 5-hour or weekly limit instead,
or the later one if both are, and submits a minute after it resets.
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

## Choose a chat

Add `--chat` to `--time`, `--reset`, `--now`, `--check` or `--inspect` to
open a specific chat first, by id, id prefix or name; the starts of the name's
words in any order also work, such as `explore pwa`. `--list [text]` shows your
recent chats with their ids and last activity. When several chats match, they
are listed instead of guessing. The chat is confirmed by its name in the window
title, checked again just before typing and before Return, so a chat whose
name another chat shares is refused until one is renamed. Chats on another Mac
that the app reaches through remote control are listed as `(remote)` and can
be chosen the same way; they run on that Mac, but this Mac must be awake and
unlocked. If that Mac is disconnected, scheduled attempts retry without typing
anything.

Use `--project name` instead of `--chat` to open the most recently active chat
in a project, matched by its folder name the same way, such as `local-relay`
for a remote project. A scheduled run picks that chat when it starts, so a chat
started after scheduling is used. `--check` and `--inspect` also report the
window title and, when exactly one Codex chat has that name, the chat's id,
project and folder.

## Delivery

A chat on this Mac, chosen with `--chat` or `--project`, gets its message
through the Codex queue instead of the input field. It works while the screen
is locked, needs no Accessibility permission, and leaves any draft alone. The
app sends the message within about ten seconds if it has the chat loaded and
not busy. If it hasn't taken the message within two minutes, the message is
removed from the queue and typed into the input field instead. Scheduling and
`--now` say which way they send. Remote chats and the open chat without
`--chat` always use the input field.

If Codex is still replying in that chat, `continue` is dropped, while a
`--message` is queued to follow the reply. When it's unclear whether a queued
message went out, the run stops instead of typing it again. The log then says
whether the reply finished or ended with an error, such as a usage limit.

Add `--message "text"` to `--time`, `--reset` or `--now` to send that text
instead. It must be one line and not start with `/`. When typed into the input
field and it already holds text, the message is added after it with a blank
line between, and both are sent together. Without a recognised input field
nothing is sent.

## Requirements and logs

Prevents idle sleep while waiting but lets the display turn off. Closing the lid
still puts the Mac to sleep; a run that starts late says so in the log. The
input field path wakes the display while it runs, briefly takes app focus
to submit, and requires an unlocked session and Accessibility permission. The
first scheduled attempt runs regardless of recent activity; up to ten
one-minute retries require one minute of inactivity and no previous input sent.
Logs: `~/Library/Logs/codex-resume.log`. Requirements and implementation notes
are in [`scripts/codex-resume.py`](../scripts/codex-resume.py).
