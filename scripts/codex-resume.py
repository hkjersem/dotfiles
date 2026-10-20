#!/usr/bin/env python3
"""Schedule a one-time submission in the Codex desktop app.

A chat on this Mac, chosen with --chat or --project, gets its message through
the Codex queue: the app server's experimental thread/queue/add, reached
through the Codex CLI. The app checks the queue about every ten seconds and
sends the message once it has the chat loaded and not busy, without
Accessibility, focus or an unlocked screen. A draft in the composer is left
alone and the message is sent by itself. If queueing is rejected, or the app
has not taken the message within two minutes, it is removed from the queue
and the Accessibility path below is used instead. Remote-control chats, and
the open chat when neither option is given, use Accessibility.
Whether Codex is replying comes from the chat's session log in
~/.codex/sessions: a reply started and not ended, in a log written within 30
minutes. Then continue is dropped, while a --message is queued for the app to
send after the reply. If it is unclear whether a queued message was sent, it
stops instead of typing it, since typing would likely fail on a locked screen
and could send it twice. After the app takes the message, the log shows for up
to 30 seconds whether the reply finished, stopped, or ended with an error such
as a usage limit.

Accessibility requires macOS, Python 3, Swift (Xcode Command Line Tools), and
Accessibility permission for the execution context. Terminal permission may
not cover launchd.

The user LaunchAgent starts immediately under caffeinate -i, or -di when the
target needs Accessibility (a remote chat or the open chat), so the display
stays on and an idle lock does not start before the run; it waits until three
seconds after the requested local minute, then sends. Accessibility runs of
lib/codex-resume.swift go under caffeinate -du, which wakes the display and
keeps it on until the helper ends. Late starts submit immediately and are
logged as late. Completion, failure, or cancellation unloads the job and
releases the sleep assertions. --now does not inhibit idle sleep.
This does not unlock the Mac or prevent manual/policy locks or lid-close sleep.
The first scheduled attempt runs regardless of recent keyboard/mouse activity.
Failures known to precede all input can retry once per minute, up to ten times;
only retries require 60 seconds of inactivity. Any possibly injected text or
Return prevents automatic retries to avoid duplicates.

The helper locates the Codex app by bundle ID (even when named ChatGPT),
excluding the separate ChatGPT chat app. It reads the composer and nearby Send
control, preserving drafts or inserting continue when empty. An empty composer
may show a voice chat button instead of Send; text is then typed, and Return
waits for Send to replace it. --message replaces continue with a single-line
custom message. After a draft, it moves the caret to the end (Cmd+Down), adds a
blank line (Shift+Return twice) and types the message, so both are sent
together; Return follows only when the composer shows exactly the draft and the
message. An unrecognised composer stops before any input, retryably when
scheduled. This app exposes its placeholder (sometimes with a newline) as
AXValue when empty. A placeholder match ignoring surrounding whitespace is
treated as empty, even if deliberately typed. If no composer label is
recognised, it activates the app and sends only Return to its existing focus,
without typing text. This fallback cannot verify what has focus or whether a
message was sent, and never retries after Return. When the window exposes no
text areas at all, scheduled runs retry first and use this fallback only on the
final attempt; --now uses it immediately. Unreadable or ambiguous controls still
fail; they do not use the fallback.

Submission activates the app and verifies composer focus and unchanged text.
For an empty composer it types continue using keyboard events, since setting
AXValue did not enable Send. If typing fails, the text is left in place and
reported, never cleared over potential user edits. In the recognised-composer
path Return is sent only when Send is enabled. AXPress on Send did not submit
in live tests. The helper attempts to restore the previous app if Codex is still
active. Inspection/checks do not activate the app. Scheduling does not pin a thread;
leave the intended conversation open in an unlocked graphical session.
Secure Keyboard Entry in another app can block macOS from activating Codex.
The helper reports its owner and refuses rather than bypassing that protection.

UI confirmation is not proof of server acceptance or a reset usage limit.
An unconfirmed attempt may have sent the message: check before retrying.
Scheduled output/errors go to ~/Library/Logs/codex-resume.log, without drafts
or custom messages. A scheduled message is stored in the LaunchAgent plist.
The helper uses current Swift syntax; when it fails to compile on Swift older
than 6.2, the error says to update the Command Line Tools.
Codex's web view builds its Accessibility tree only on request, so the helper
turns AXEnhancedUserInterface on while it runs (about two seconds to appear) and
off again afterwards, unless it was already on.
App updates or UI language changes can affect control discovery.

--start-window reads the limits when the run starts and sends nothing while a
5-hour window is running or ordinary usage is blocked. A 5-hour window counts
as not running when it is absent, has reset, or reports 0% with a reset a full
window ahead; the live response without a running window is unverified.

--list shows your unarchived chats from the Codex app's sidebar catalogue,
newest first, without subagent or review threads. It includes chats on another
Mac the app reaches through remote control, marked (remote): they run on that
Mac, but this Mac must be awake and unlocked to type into them. --chat picks
one by id, id prefix, or name: the whole name, part of it, or the starts of its
words in any order; several matches are listed with their last activity
instead of guessing. The helper opens it through its codex://threads/<id> link
(with ?hostId= for a remote chat) only after any idle check passes, then waits
for the window title to show the chat's name, and checks it again just before
typing and before Return. A chat whose name another chat shares is refused,
since the title could not tell them apart. A wrong title or a disabled
composer, as when the remote Mac is disconnected, stops before any input and
is retryable.
--project picks a project by its folder name (or full path) with the same
name matching, and opens its most recently active chat; a scheduled run stores
the folder and picks that chat when it starts.
--check and --inspect add the window title and, when exactly one chat in the
catalogue has that name, the open chat's id, project and folder.
--limits and --reset read the usage limits live through the Codex CLI's app
server (account/rateLimits/read, read-only). --reset schedules a minute after
the latest reset among the limits that are used up. --reset MINUTES also reads
the limits that often while waiting, logging them each time. It sends once no
limit is used up and the limits that were used up when it was scheduled show 0%
used, so an early reset is used too; if those already show use, someone started
on the new allowance elsewhere and it stops without sending. At the reset time
it sends unless that is the case, also when the limits cannot be read.
"""

from datetime import datetime, timedelta
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid


LABEL = "com.hkjersem.codex-resume"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/codex-resume.log"
SCRIPT_PATH = Path(__file__).resolve()
TIME_PATTERN = re.compile(r"([01][0-9]|2[0-3])[:.]?([0-5][0-9])\Z")
SCHEDULE_DELAY_SECONDS = 3
RESET_MARGIN_SECONDS = 60
MIN_RESET_CHECK_MINUTES = 5
# A check or send this far past its planned time is logged as late, as after sleep.
LATE_SECONDS = 120
CODEX_HOME = Path.home() / ".codex"
CHAT_CATALOG = "sqlite/codex-dev.db"
CODEX_CLI_PATHS = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
)
APP_SERVER_TIMEOUT_SECONDS = 20
LIST_LIMIT = 20
LIMIT_NAMES = {300: "5-hour", 10080: "weekly"}
FIVE_HOUR_MINUTES = 300
# An unused window may report 0% with a reset a full window ahead; allow for clock and rounding drift.
IDLE_WINDOW_TOLERANCE_SECONDS = 120
DEFAULT_MESSAGE = "continue"
# The app checks the queue every ten seconds, and only for a chat it has loaded that is not busy.
QUEUE_PICKUP_SECONDS = 120
QUEUE_POLL_SECONDS = 5
TURN_EVENTS = ("task_started", "task_complete", "turn_aborted")
# An unfinished reply this old in the session log is treated as abandoned, as after a crash.
REPLY_STALE_SECONDS = 30 * 60
REPLY_RESULT_SECONDS = 30
RETRY_INTERVAL_SECONDS = 60
MAX_SCHEDULED_RETRIES = 10
HELPER_PATH = SCRIPT_PATH.parent / "lib/codex-resume.swift"
SCHEDULED_ACTIONS = (
    "--scheduled-initial-submit", "--scheduled-submit", "--scheduled-final-submit",
)
MIN_SWIFT_VERSION = (6, 2)
SWIFT_VERSION_PATTERN = re.compile(r"Swift version (\d+)\.(\d+)")
SWIFT_COMPILE_ERROR_PATTERN = re.compile(r"\.swift:\d+:\d+: error:")


class RetryableAutomationError(RuntimeError):
    """The native helper confirms that no input was injected."""


class AppServerRejectedError(RuntimeError):
    """The app server could not be started or answered with an error, so it did nothing."""


def next_occurrence(clock_text, now=None):
    match = TIME_PATTERN.fullmatch(clock_text)
    if match is None:
        raise ValueError(
            f"invalid time {clock_text!r}: expected 24-hour HH:MM, HH.MM, or HHMM "
            "(for example, 15:03, 15.03, or 1503)"
        )
    now = now or datetime.now().astimezone()
    hour, minute = (int(part) for part in match.groups())
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled


def validate_message(text):
    message = text.strip()
    if not message:
        raise ValueError("--message must not be empty.")
    if any(unicodedata.category(character) == "Cc" for character in message):
        raise ValueError(
            "--message must be a single line without control characters, "
            "since a typed newline would send it early."
        )
    if message.startswith("/"):
        raise ValueError("--message must not start with '/', which opens the app's command menu.")
    return message


def format_time(moment):
    return moment.strftime("%Y-%m-%d %H:%M")


def load_chats():
    # The app's sidebar catalogue: unarchived chats on this Mac and on Macs it reaches by remote control.
    query = """
        SELECT c.thread_id, c.display_title, c.cwd,
               COALESCE(NULLIF(c.source_recency_at, 0), c.source_updated_at),
               CASE h.host_kind WHEN 'local' THEN NULL ELSE c.host_id END
        FROM local_thread_catalog c JOIN local_thread_catalog_hosts h USING (host_id)
        WHERE h.host_kind IN ('local', 'remote-control') AND c.missing_candidate = 0
          AND COALESCE(c.thread_source, '') NOT IN ('subagent', 'guardian_review')
        ORDER BY 4 DESC
    """
    path = CODEX_HOME / CHAT_CATALOG
    if not path.exists():
        raise RuntimeError(f"Codex's chat list ({path}) was not found; open the Codex app first.")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            rows = connection.execute(query).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError(f"Could not read Codex chats from {path}: {error}") from error
    return [
        {
            "id": chat_id,
            "name": " ".join((name or "").split()),
            "cwd": cwd,
            "updated": datetime.fromtimestamp(updated).astimezone(),
            "host": host,
        }
        for chat_id, name, cwd, updated, host in rows
    ]


def describe_chats(chats):
    width = max((len(chat["name"]) for chat in chats), default=0)
    width = min(width, 60)
    lines = []
    for chat in chats:
        name = chat["name"] if len(chat["name"]) <= width else chat["name"][:width - 1] + "…"
        folder = Path(chat["cwd"]).name if chat["cwd"] else ""
        if chat["host"] is not None:
            folder = f"{folder} (remote)".lstrip()
        lines.append(f"{format_time(chat['updated'])}  {chat['id']}  {name:<{width}}  {folder}".rstrip())
    return "\n".join(lines)


def name_words(name):
    return re.findall(r"\w+", name.casefold())


def first_matches(items, rules):
    for rule in rules:
        found = [item for item in items if rule(item)]
        if found:
            return found
    return []


def name_rules(query):
    needle = query.strip().casefold()
    typed = name_words(needle)
    return (
        lambda item: item["name"].casefold() == needle,
        lambda item: needle in item["name"].casefold(),
        # Each typed word starts a word in the name, in any order: "explore pwa", "exp prod".
        lambda item: bool(typed) and all(
            any(word.startswith(part) for word in name_words(item["name"])) for part in typed
        ),
    )


def matching_chats(query, chats):
    return first_matches(chats, (
        lambda chat: chat["id"] == query.strip(),
        lambda chat: chat["id"].startswith(query.strip().casefold()),
        *name_rules(query),
    ))


def same_title(first, second):
    """Mirror the helper's window-title check, where either name may be shortened with an ellipsis."""
    first, second = " ".join(first.split()), " ".join(second.split())
    if first == second:
        return True
    for shortened, full in ((first, second), (second, first)):
        stem = shortened[:-1].rstrip() if shortened.endswith("…") else ""
        if stem and full.startswith(stem):
            return True
    return False


def resolve_chat(query):
    chats = load_chats()
    found = matching_chats(query, chats)
    if not found:
        raise RuntimeError(f"No chat matches {query!r}; run codex-resume --list to see your chats.")
    if len(found) > 1:
        raise RuntimeError(
            f"{len(found)} chats match {query!r}; pass --chat with one of these ids:\n"
            + describe_chats(found)
        )
    return confirmable(found[0], chats)


def confirmable(chat, chats):
    # The helper confirms the open chat by its window title, so a shared name could hide a wrong chat.
    namesakes = [other for other in chats if other is not chat and same_title(other["name"], chat["name"])]
    if namesakes:
        raise RuntimeError(
            f"Another chat has the same name as {chat['name']!r}, so the window title cannot confirm "
            "which one is open; rename one of them in the app first:\n" + describe_chats([chat, *namesakes])
        )
    return chat


def project_label(project):
    return f"{project['name']!r}" + (" (remote)" if project["host"] is not None else "")


def load_projects(chats):
    """Group chats by the folder the app names their project after, newest activity first."""
    projects = {}
    for chat in chats:
        if chat["cwd"]:
            key = (chat["host"], chat["cwd"])
            projects.setdefault(key, {
                "name": Path(chat["cwd"]).name, "cwd": chat["cwd"], "host": chat["host"], "chats": [],
            })["chats"].append(chat)
    return list(projects.values())


def resolve_project(query):
    """Return the latest chat in the project matching query, by folder path or project name."""
    chats = load_chats()
    found = first_matches(load_projects(chats), (lambda project: project["cwd"] == query.strip(), *name_rules(query)))
    if not found:
        raise RuntimeError(f"No project matches {query!r}; run codex-resume --list to see your chats' projects.")
    if len(found) > 1:
        raise RuntimeError(
            f"{len(found)} projects match {query!r}; pass --project with one of these folders:\n"
            + "\n".join(f"{project['cwd']}  {project_label(project)}" for project in found)
        )
    project = found[0]
    return project, confirmable(project["chats"][0], chats)


def list_chats(query=None):
    chats = load_chats()
    if query:
        chats = matching_chats(query, chats)
    if not chats:
        print("No matching chats." if query else "No chats found.")
        return
    print(describe_chats(chats[:LIST_LIMIT]))
    if len(chats) > LIST_LIMIT:
        print(f"… and {len(chats) - LIST_LIMIT} older; add a search term to narrow the list.")


def codex_cli():
    for path in CODEX_CLI_PATHS:
        if os.access(path, os.X_OK):
            return str(path)
    found = shutil.which("codex")
    if found is None:
        raise AppServerRejectedError("The Codex CLI is unavailable, so its app server cannot be reached.")
    return found


def app_server_request(method, params=None):
    """Send one JSON-RPC request to a short-lived app server and return its result."""
    try:
        process = subprocess.Popen(
            [codex_cli(), "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except OSError as error:
        raise AppServerRejectedError(f"The Codex app server could not be started: {error}") from error
    replies = {}
    received = threading.Event()

    def read():
        try:
            for line in process.stdout:
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if reply.get("id") == 2:
                    replies["reply"] = reply
                    return
        finally:
            received.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        try:
            # The server exits at end of input before answering, so stdin stays open until the reply.
            for request in (
                # The queue methods are experimental and need this opt-in.
                {"id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "codex-resume", "version": "1"},
                            "capabilities": {"experimentalApi": True}}},
                {"method": "initialized"},
                {"id": 2, "method": method, **({"params": params} if params is not None else {})},
            ):
                process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        except OSError as error:
            # The server may have read the request before it went away, so whether it ran is unknown.
            raise RuntimeError(f"The Codex app server stopped while sending {method}: {error}") from error
        if not received.wait(APP_SERVER_TIMEOUT_SECONDS):
            raise RuntimeError(f"The Codex app server did not answer {method} in time.")
    finally:
        process.kill()
        process.wait()
    reply = replies.get("reply")
    if reply is None:
        raise RuntimeError(f"The Codex app server exited without answering {method}.")
    if "error" in reply:
        raise AppServerRejectedError(
            f"The Codex app server rejected {method}: {reply['error'].get('message', reply['error'])}")
    return reply["result"]


def uses_queue(target):
    """A chat or project on this Mac takes a queued message; a remote one needs the app's controls here."""
    return target is not None and target["host"] is None


def delivery_label(target):
    return "via the Codex queue" if uses_queue(target) else "via the app's Accessibility controls"


def still_queued(chat, item):
    params = {"threadId": chat["id"]}
    while True:
        page = app_server_request("thread/queue/list", params)
        if any(entry.get("id") == item for entry in page.get("data") or []):
            return True
        if not page.get("nextCursor"):
            return False
        params = {**params, "cursor": page["nextCursor"]}


def chat_log(chat):
    """The session log the app writes for a chat on this Mac, or None when it cannot be found."""
    found = list((CODEX_HOME / "sessions").glob(f"*/*/*/rollout-*-{chat['id']}.jsonl"))
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def written_since(record, since):
    """Whether a session log record is stamped at or after since; an unreadable stamp counts as older."""
    try:
        return datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")) >= since
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def turn_events(path, offset=0, since=None):
    """Reply start and end events in a session log, from a byte offset and, if given, a time."""
    events = []
    with path.open("rb") as log:
        log.seek(offset)
        for line in log:
            if b'"event_msg"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get("payload") or {}
            if since is not None and not written_since(record, since):
                continue
            if record.get("type") == "event_msg" and payload.get("type") in TURN_EVENTS:
                events.append(payload)
    return events


def replying(chat, now=None):
    """Whether Codex is writing a reply in the chat: its last reply started and has not ended."""
    path = chat_log(chat)
    # A log left unfinished by a crash would otherwise look busy forever.
    if path is None or (now or time.time()) - path.stat().st_mtime > REPLY_STALE_SECONDS:
        return False
    events = turn_events(path)
    return bool(events) and events[-1]["type"] == "task_started"


def report_reply(path, offset, stamp, since=None):
    """Log how the reply the queued message started has ended so far, such as at a usage limit."""
    if path is None:
        return
    deadline = time.monotonic() + REPLY_RESULT_SECONDS
    while True:
        ended = [event for event in turn_events(path, offset, since) if event["type"] != "task_started"]
        if ended:
            error = ended[0].get("error")
            if ended[0]["type"] == "turn_aborted":
                print(f"{stamp()}The reply was stopped.", flush=True)
            elif error:
                detail = error.get("message", error) if isinstance(error, dict) else error
                print(f"{stamp()}The reply ended with an error: {detail}", flush=True)
            else:
                print(f"{stamp()}The reply finished.", flush=True)
            return
        if time.monotonic() >= deadline:
            print(f"{stamp()}Codex is replying.", flush=True)
            return
        time.sleep(QUEUE_POLL_SECONDS)


def resume_through_queue(chat, message, stamp=lambda: ""):
    """Send the message through the queue; return False, with nothing left queued, to use the app's controls."""
    busy = replying(chat)
    if busy and message is None:
        print(f"{stamp()}Codex is already replying in chat {chat_label(chat)}; not sending {DEFAULT_MESSAGE}.",
              flush=True)
        return True
    path = chat_log(chat)
    offset = path.stat().st_size if path is not None else 0
    # Session logs stamp to the millisecond, so a whole second keeps the comparison from cutting a reply off.
    queued_at = datetime.now().astimezone().replace(microsecond=0)
    try:
        item = app_server_request("thread/queue/add", {
            "threadId": chat["id"],
            "input": [{"type": "text", "text": message or DEFAULT_MESSAGE, "text_elements": []}],
            "clientUserMessageId": str(uuid.uuid4()),
        })["queuedSubmission"]["id"]
    except AppServerRejectedError as error:
        print(f"{stamp()}Could not queue the message ({error}); using the app's Accessibility controls instead.",
              flush=True)
        return False
    except RuntimeError as error:
        raise RuntimeError(f"The message may have been queued: {error} Check the chat before retrying.") from error
    if busy:
        print(f"{stamp()}Codex is replying in chat {chat_label(chat)}; queued the message, and the app "
              "sends it when the reply ends.", flush=True)
        return True
    print(f"{stamp()}Queued the message in chat {chat_label(chat)}; waiting for the app to take it.", flush=True)
    deadline = time.monotonic() + QUEUE_PICKUP_SECONDS
    try:
        while still_queued(chat, item):
            if time.monotonic() >= deadline:
                deleted = app_server_request(
                    "thread/queue/delete", {"threadId": chat["id"], "queuedSubmissionId": item})
                if deleted.get("deleted"):
                    print(f"{stamp()}The app did not take the message within {QUEUE_PICKUP_SECONDS // 60} "
                          "minutes, so it was removed from the queue; using the app's Accessibility controls "
                          "instead.", flush=True)
                    return False
                break
            time.sleep(QUEUE_POLL_SECONDS)
    except RuntimeError as error:
        raise RuntimeError(f"The message was queued, but whether the app took it is unknown: {error} "
                           "Check the chat before retrying.") from error
    print(f"{stamp()}The app took the message from the queue.", flush=True)
    if path is not None:
        report_reply(path, offset, stamp)
    else:
        # Codex compresses idle chats' logs and unpacks one again before writing to it, so a log found only
        # now holds earlier replies too; only events from after queueing belong to this one.
        report_reply(chat_log(chat), 0, stamp, since=queued_at)
    return True


def usage_limits():
    limits = app_server_request("account/rateLimits/read").get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not window or window.get("resetsAt") is None:
            continue
        minutes = window.get("windowDurationMins")
        windows.append({
            "name": LIMIT_NAMES.get(minutes, f"{minutes}-minute"),
            "minutes": minutes,
            "used": window.get("usedPercent", 0),
            "resets": datetime.fromtimestamp(window["resetsAt"]).astimezone(),
        })
    if not windows:
        raise RuntimeError("Codex reported no usage limits.")
    return windows


def start_window_skip_reason(now=None):
    """Return why --start-window should not send now, or None when sending starts a new 5-hour window."""
    result = app_server_request("account/rateLimits/read")
    limits = result.get("rateLimits") or {}
    now = now or datetime.now().astimezone()
    for key in ("primary", "secondary"):
        window = limits.get(key) or {}
        if window.get("windowDurationMins") != FIVE_HOUR_MINUTES or window.get("resetsAt") is None:
            continue
        resets = datetime.fromtimestamp(window["resetsAt"]).astimezone()
        used = window.get("usedPercent", 0)
        unused = used == 0 and resets >= now + timedelta(
            minutes=FIVE_HOUR_MINUTES, seconds=-IDLE_WINDOW_TOLERANCE_SECONDS)
        if resets > now and not unused:
            return f"a 5-hour window is already running ({used:g}% used, resets {format_time(resets)})"
    if result.get("ordinaryUsageAllowed") is False:
        return "a usage limit is used up, so a message cannot start a window"
    return None


def describe_limits(windows):
    return "\n".join(
        f"{window['name']}: {window['used']:g}% used, resets {format_time(window['resets'])}"
        for window in windows
    )


def reset_time(windows):
    exhausted = [window for window in windows if window["used"] >= 100]
    if not exhausted:
        raise RuntimeError("No Codex usage limit is used up, so there is no reset to wait for.\n"
                           + describe_limits(windows))
    return max(window["resets"] for window in exhausted) + timedelta(seconds=RESET_MARGIN_SECONDS)


def late_note(planned, now=None):
    """Say how late a run is when it started well after its planned time, as when the Mac was asleep."""
    late = (now or datetime.now(planned.tzinfo)) - planned
    if late.total_seconds() < LATE_SECONDS:
        return ""
    hours, minutes = divmod(int(late.total_seconds()) // 60, 60)
    duration = f"{hours} h {minutes} min" if hours else f"{minutes} min"
    return f"{duration} late; the Mac was probably asleep or off"


def wait_until_scheduled_time(timestamp, now=None):
    scheduled = datetime.fromisoformat(timestamp) + timedelta(seconds=SCHEDULE_DELAY_SECONDS)
    now = now or datetime.now(scheduled.tzinfo)
    delay = (scheduled - now).total_seconds()
    if delay > 0:
        time.sleep(delay)


def early_reset_state(used_up):
    """Read the limits for a --reset MINUTES job: "wait" while a limit is used up, "send" once the limits
    that were used up have reset with nothing used since, or "taken" when that new allowance is already in use.
    used_up lists the window lengths in minutes that were used up when the job was scheduled."""
    result = app_server_request("account/rateLimits/read")
    limits = result.get("rateLimits") or {}
    windows = {}
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if window and window.get("windowDurationMins") is not None:
            windows[window["windowDurationMins"]] = window
    parts = []
    for minutes, window in windows.items():
        resets = window.get("resetsAt")
        when = format_time(datetime.fromtimestamp(resets).astimezone()) if resets is not None else "not set"
        parts.append(f"{LIMIT_NAMES.get(minutes, f'{minutes}-minute')}: "
                     f"{window.get('usedPercent', 0):g}% used, resets {when}")
    if result.get("ordinaryUsageAllowed") is False:
        parts.append("usage not allowed")
    detail = "; ".join(parts) or "no limits reported"
    if result.get("ordinaryUsageAllowed") is False or any(
            window.get("usedPercent", 0) >= 100 for window in windows.values()):
        return "wait", detail
    if any(windows.get(minutes, {}).get("usedPercent", 0) > 0 for minutes in used_up):
        return "taken", detail
    return "send", detail


def wait_for_early_reset(timestamp, minutes, used_up):
    """Check the limits every few minutes until the used-up limits reset, early or at the scheduled time.
    Return whether to send: not when the new allowance is already in use, as when a session was started
    on another device."""
    deadline = datetime.fromisoformat(timestamp) + timedelta(seconds=SCHEDULE_DELAY_SECONDS)
    interval = timedelta(minutes=minutes)
    next_check = datetime.now(deadline.tzinfo) + interval
    while True:
        final = next_check >= deadline
        if final:
            wait_until_scheduled_time(timestamp)
        else:
            time.sleep(max(0, (next_check - datetime.now(deadline.tzinfo)).total_seconds()))
        late = late_note(deadline if final else next_check)
        try:
            state, detail = early_reset_state(used_up)
        except RuntimeError as error:
            state, detail = "unknown", f"the limits could not be read: {error}"
        if late:
            detail += f"; {late}"
        if state == "taken":
            print(f"{log_stamp()}The limit has reset, but its new allowance is already in use ({detail}); "
                  "not sending.", flush=True)
            return False
        if state == "send":
            print(f"{log_stamp()}The limit has reset{'' if final else ' early'} and its new allowance is unused "
                  f"({detail}); sending.", flush=True)
            return True
        if final:
            print(f"{log_stamp()}The reset time has arrived ({detail}); sending.", flush=True)
            return True
        next_check = max(next_check + interval, datetime.now(deadline.tzinfo))
        then = (f"next check at {format_time(next_check)}" if next_check < deadline
                else f"last check at the reset, {format_time(deadline)}")
        print(f"{log_stamp()}Not sending yet ({detail}); {then}.", flush=True)


def launchctl(*arguments):
    executable = shutil.which("launchctl")
    if executable is None:
        raise RuntimeError("launchctl is unavailable; this command requires macOS.")
    result = subprocess.run([executable, *arguments], text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"launchctl {' '.join(arguments)} failed: {detail}")
    return result


def agent_domain():
    return f"gui/{os.getuid()}"


def remove_agent():
    if not PLIST_PATH.exists():
        return False
    launchctl("bootout", f"{agent_domain()}/{LABEL}")
    PLIST_PATH.unlink()
    return True


def schedule(scheduled, message=None, chat=None, project=None, start_window=False, every=None, used_up=()):
    if PLIST_PATH.exists():
        remove_agent()

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            # Typing needs an unlocked screen, so keep the display on and the idle lock off until the run;
            # a queued message needs neither.
            "/usr/bin/caffeinate",
            "-i" if uses_queue(project or chat) else "-di",
            sys.executable,
            str(SCRIPT_PATH),
            *(["--message", message] if message is not None else []),
            # A project keeps its folder, so the latest chat is chosen when the run starts.
            *(["--project", project["cwd"]] if project is not None
              else ["--chat", chat["id"]] if chat is not None else []),
            *(["--start-window"] if start_window else []),
            *(["--every", str(every), "--used-up", ",".join(str(minutes) for minutes in used_up)]
              if every else []),
            "--scheduled",
            scheduled.isoformat(),
        ],
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=PLIST_PATH.parent, prefix=f".{LABEL}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        plistlib.dump(plist, temporary)
    temporary_path.chmod(0o600)
    temporary_path.replace(PLIST_PATH)

    try:
        launchctl("bootstrap", agent_domain(), str(PLIST_PATH))
    except RuntimeError:
        PLIST_PATH.unlink(missing_ok=True)
        raise

    print(scheduled_summary(scheduled, message, None if project is not None else chat, project, start_window,
                            every))


def chat_label(chat):
    return f"{chat['name']!r}" + (" (remote)" if chat["host"] is not None else "")


def scheduled_summary(scheduled, message, chat=None, project=None, start_window=False, every=None):
    summary = f"Codex resume scheduled for {format_time(scheduled)}"
    if every:
        summary += f", or earlier if the limit resets first (checked every {every} minutes)"
    if project is not None:
        summary += f" in the latest chat in project {project_label(project)}"
    elif chat is not None:
        summary += f" in chat {chat_label(chat)}"
    if start_window:
        summary += ", only if no 5-hour window is running"
    summary += f" {delivery_label(project or chat)}"
    return f"{summary} with message: {message}" if message is not None else f"{summary}."


def status():
    if not PLIST_PATH.exists():
        print("No Codex resume is scheduled.")
        return
    with PLIST_PATH.open("rb") as agent_file:
        plist = plistlib.load(agent_file)
    arguments = plist.get("ProgramArguments", [])
    scheduled_at = arguments[arguments.index("--scheduled") + 1]
    scheduled = datetime.fromisoformat(scheduled_at)
    message = arguments[arguments.index("--message") + 1] if "--message" in arguments else None
    chat = project = None
    if "--project" in arguments:
        folder = arguments[arguments.index("--project") + 1]
        unknown = {"name": Path(folder).name, "cwd": folder, "host": None}
        try:
            project = next((project for project in load_projects(load_chats()) if project["cwd"] == folder), unknown)
        except RuntimeError:
            project = unknown
    if "--chat" in arguments:
        chat_id = arguments[arguments.index("--chat") + 1]
        unknown = {"id": chat_id, "name": chat_id, "host": None}
        try:
            chat = next((chat for chat in load_chats() if chat["id"] == chat_id), unknown)
        except RuntimeError:
            chat = unknown
    every = int(arguments[arguments.index("--every") + 1]) if "--every" in arguments else None
    print(scheduled_summary(scheduled, message, chat, project, "--start-window" in arguments, every))


def swift_version(executable):
    try:
        result = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = SWIFT_VERSION_PATTERN.search(result.stdout + result.stderr)
    return tuple(int(part) for part in match.groups()) if match else None


def run_automation(action, message=None, chat=None):
    executable = shutil.which("swift")
    if executable is None:
        raise RuntimeError(
            "Swift is unavailable; install Apple's Xcode Command Line Tools."
        )
    command = [executable, str(HELPER_PATH), action]
    if action in SCHEDULED_ACTIONS:
        # The scheduled job only holds off idle sleep, so wake the display and keep it on for the helper.
        command = ["/usr/bin/caffeinate", "-du", *command]
    if message is not None:
        command += ["--message", message]
    if chat is not None:
        command += ["--chat", chat["id"], "--chat-title", chat["name"]]
        if chat["host"] is not None:
            command += ["--chat-host", chat["host"]]
    try:
        result = subprocess.run(
            command,
            text=True, capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Accessibility automation timed out; submission state is unknown. "
            "Check the app before retrying."
        ) from error
    if result.returncode == 3:
        raise RuntimeError(
            "Submission was attempted, but could not be confirmed. "
            "The message may have been sent; check the app before retrying. "
            + (result.stderr or result.stdout).strip()
        )
    if result.returncode == 4 and action in SCHEDULED_ACTIONS:
        raise RetryableAutomationError((result.stderr or result.stdout).strip())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if SWIFT_COMPILE_ERROR_PATTERN.search(detail):
            version = swift_version(executable)
            if version is not None and version < MIN_SWIFT_VERSION:
                raise RuntimeError(
                    f"Swift {version[0]}.{version[1]} cannot compile the helper; it needs Swift "
                    f"{MIN_SWIFT_VERSION[0]}.{MIN_SWIFT_VERSION[1]} or newer. Update the Command "
                    "Line Tools (dotfiles-update does this), then try again. Nothing was sent."
                )
        raise RuntimeError(f"Codex automation failed: {detail}")
    if action in ("--check", "--inspect"):
        report = json.loads(result.stdout)
        report["chat"] = identify_open_chat(report.get("windowTitle"))
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), flush=True)
        return
    print(result.stdout, end="", flush=True)


def identify_open_chat(title):
    """Identify the chat the window shows, when exactly one chat has that name."""
    try:
        found = [chat for chat in load_chats() if title and same_title(chat["name"], title)]
    except RuntimeError:
        return None
    if len(found) != 1:
        return None
    chat = found[0]
    return {
        "id": chat["id"], "name": chat["name"], "remote": chat["host"] is not None,
        "project": Path(chat["cwd"]).name if chat["cwd"] else None, "folder": chat["cwd"],
    }


def submit(action="--submit", message=None, chat=None):
    run_automation(action, message, chat)


def scheduled_action(attempt):
    if attempt == 0:
        return "--scheduled-initial-submit"
    if attempt == MAX_SCHEDULED_RETRIES:
        return "--scheduled-final-submit"
    return "--scheduled-submit"


def log_stamp():
    return f"[{datetime.now():%d.%m.%Y kl. %H.%M.%S}] "


def submit_scheduled(message=None, chat=None):
    for attempt in range(MAX_SCHEDULED_RETRIES + 1):
        started = time.monotonic()
        print(
            log_stamp()
            + f"Scheduled Codex submission attempt {attempt + 1}/"
            f"{MAX_SCHEDULED_RETRIES + 1}"
            + (f" in chat {chat_label(chat)}." if chat is not None else "."),
            flush=True,
        )
        try:
            submit(scheduled_action(attempt), message, chat)
            return
        except RetryableAutomationError as error:
            print(f"codex-resume: {error}", file=sys.stderr, flush=True)
            if attempt == MAX_SCHEDULED_RETRIES:
                raise RuntimeError(
                    "Scheduled retry window exhausted; nothing was submitted."
                ) from error
            delay = RETRY_INTERVAL_SECONDS - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)


def argument_parser():
    parser = argparse.ArgumentParser(
        prog="codex-resume",
        description=(
            "Schedule or control a one-time submission in the "
            "Codex desktop app. Times use local 24-hour HH:MM, HH.MM, or HHMM format."
        ),
        epilog=(
            "A chat on this Mac chosen with --chat or --project gets its message through "
            "the Codex queue, which needs no Accessibility or unlocked screen and leaves "
            "any draft alone; if the app has not taken it within two minutes, it is "
            "removed and the steps below are used instead. "
            "Submission activates the app and sends Return, verifying composer focus "
            "when recognised. If unrecognised, it sends only Return to the app's "
            "existing focus, without typing text or retrying after Return. "
            "Inspection does not activate the app. "
            "An empty composer is filled with 'continue', or with --message; existing "
            "drafts are preserved, and --message is added after a draft with a blank line. "
            "Scheduled jobs inhibit idle system/display sleep while waiting, "
            "but cannot prevent manual or policy-enforced locking. "
            "The first scheduled attempt ignores recent activity. Up to ten "
            "one-minute retries require 60 seconds of inactivity and are allowed "
            "only when no input has been sent."
        ),
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--time", metavar="HH:MM", help="schedule the next occurrence of this local time"
    )
    actions.add_argument(
        "--reset", nargs="?", const=0, type=int, metavar="MINUTES",
        help="schedule a minute after the used-up usage limit resets (the later one if both are); "
             f"with MINUTES (at least {MIN_RESET_CHECK_MINUTES}), also check that often and send "
             "as soon as the limit resets early; skip if its new allowance is already in use",
    )
    actions.add_argument(
        "--limits", action="store_true", help="show the 5-hour and weekly usage limits",
    )
    actions.add_argument(
        "--list", nargs="?", const="", metavar="QUERY",
        help="list your recent chats, optionally only those matching QUERY",
    )
    actions.add_argument("--status", action="store_true", help="show the scheduled resume")
    actions.add_argument("--cancel", action="store_true", help="cancel the scheduled resume")
    actions.add_argument(
        "--now", action="store_true",
        help="submit the detected composer now",
    )
    actions.add_argument(
        "--inspect", action="store_true",
        help="print the composer text without modifying or submitting it",
    )
    actions.add_argument(
        "--check", action="store_true",
        help="check Accessibility controls without printing or changing the draft",
    )
    actions.add_argument("--scheduled", metavar="TIMESTAMP", help=argparse.SUPPRESS)
    parser.add_argument("--every", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--used-up", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--message", metavar="TEXT",
        help="with --time, --reset or --now, type this single-line message instead of 'continue'",
    )
    parser.add_argument(
        "--chat", metavar="QUERY",
        help="open this chat (id, id prefix or name) first; works with --time, --reset, "
             "--now, --check and --inspect",
    )
    parser.add_argument(
        "--project", metavar="QUERY",
        help="like --chat, but open the latest chat in this project (folder name), "
             "chosen when the message is sent",
    )
    parser.add_argument(
        "--start-window", action="store_true",
        help="with --time or --now, send only when no 5-hour window is running, "
             "so the message starts a new one",
    )
    return parser


def main(arguments):
    parser = argument_parser()
    options = parser.parse_args(arguments)
    message = None
    if options.message is not None:
        if not (options.time or options.reset is not None or options.now or options.scheduled):
            parser.error("--message requires --time, --reset or --now")
        message = validate_message(options.message)
    for name, value in (("--chat", options.chat), ("--project", options.project)):
        if value is not None and not (
            options.time or options.reset is not None or options.now or options.check
            or options.inspect or options.scheduled
        ):
            parser.error(f"{name} requires --time, --reset, --now, --check or --inspect")
    if options.chat is not None and options.project is not None:
        parser.error("use either --chat or --project, not both")
    if options.start_window and not (options.time or options.now or options.scheduled):
        parser.error("--start-window requires --time or --now")
    if options.reset and options.reset < MIN_RESET_CHECK_MINUTES:
        parser.error(f"--reset MINUTES must be at least {MIN_RESET_CHECK_MINUTES}")

    def skip_start_window():
        if not options.start_window:
            return False
        reason = start_window_skip_reason()
        stamp = log_stamp() if options.scheduled else ""
        if reason is not None:
            print(f"{stamp}Not sending: {reason}.", flush=True)
            return True
        print(f"{stamp}No 5-hour window is running; sending to start one.", flush=True)
        return False

    def target():
        if options.project is not None:
            return resolve_project(options.project)
        return None, resolve_chat(options.chat) if options.chat is not None else None

    if options.list is not None:
        list_chats(options.list)
        return 0
    if options.limits:
        print(describe_limits(usage_limits()))
        return 0
    if options.status:
        status()
        return 0
    if options.cancel:
        if remove_agent():
            print("Scheduled Codex resume cancelled.")
        else:
            print("No Codex resume is scheduled.")
        return 0
    if options.scheduled:
        try:
            if options.every:
                used_up = [int(minutes) for minutes in options.used_up.split(",") if minutes]
                if not wait_for_early_reset(options.scheduled, options.every, used_up):
                    return 0
            else:
                wait_until_scheduled_time(options.scheduled)
                late = late_note(datetime.fromisoformat(options.scheduled))
                if late:
                    print(f"{log_stamp()}Running now ({late}).", flush=True)
            if skip_start_window():
                return 0
            # Resolved at run time, so a project gets its latest chat and a failure still unloads the job.
            _, chat = target()
            if not (uses_queue(chat) and resume_through_queue(chat, message, log_stamp)):
                submit_scheduled(message, chat)
        except (OSError, RuntimeError, ValueError) as error:
            # bootout can terminate us before the outer error handler runs.
            print(f"codex-resume: {error}", file=sys.stderr, flush=True)
            return 1
        finally:
            if PLIST_PATH.exists():
                PLIST_PATH.unlink()
                launchctl("bootout", f"{agent_domain()}/{LABEL}")
        return 0
    project, chat = target()
    if options.now:
        if skip_start_window():
            return 0
        if chat is not None:
            where = f" in project {project_label(project)}" if project is not None else ""
            print(f"Resuming in chat {chat_label(chat)}{where} {delivery_label(chat)}.", flush=True)
        else:
            print(f"Resuming in the open chat {delivery_label(None)}.", flush=True)
        if not (uses_queue(chat) and resume_through_queue(chat, message)):
            submit(message=message, chat=chat)
        return 0
    if options.inspect:
        run_automation("--inspect", chat=chat)
        return 0
    if options.check:
        run_automation("--check", chat=chat)
        return 0
    used_up = ()
    if options.reset is not None:
        windows = usage_limits()
        scheduled = reset_time(windows)
        used_up = [window["minutes"] for window in windows if window["used"] >= 100]
        print(describe_limits(windows))
    else:
        scheduled = next_occurrence(options.time)
    schedule(scheduled, message, chat, project, options.start_window, options.reset or None, used_up)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"codex-resume: {error}", file=sys.stderr)
        sys.exit(1)
