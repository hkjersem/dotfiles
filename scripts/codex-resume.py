#!/usr/bin/env python3
"""Send continue, or a message, to a Codex desktop chat on this Mac: now, at a
set time, or when a usage limit resets.

The message goes through the Codex queue: the app server's experimental
thread/queue/add, reached through the Codex CLI bundled with the app. The app
checks the queue about every ten seconds and sends the message once the chat
is not busy and loaded, without Accessibility, focus or an unlocked screen. A
draft in the composer is left alone. When the app is not running, it is opened
in the background. If the app has not taken the message within 30 seconds, the
chat is probably not loaded, so it is opened in the background through a
codex://threads link, which switches the app to it; that is skipped while you
may be using the app: it is frontmost, the screen is unlocked, and you used the
Mac within five minutes. If the app has not taken the message within two
minutes, it stays queued and the app sends it once the chat is open. The chat is
chosen with --chat or --project; without them it is the chat on this Mac with
the latest activity, chosen when the message is sent.

Whether Codex is replying comes from the chat's session log in
~/.codex/sessions: a reply started and not ended, in a log written within 30
minutes. Then continue is dropped, while a --message is queued for the app to
send after the reply. After the app takes the message, the log shows for up to
30 seconds whether the reply finished, stopped, or ended with an error such as
a usage limit. If it is unclear whether a message was queued or taken, it says
to check the chat before retrying.

--recurring MINUTES sets up a second LaunchAgent that checks that often for
chats on this Mac stuck at a usage limit: their session log ends in replies
that stopped with codex_error_info usage_limit_exceeded. Messages sent while a
limit is used up stop at it too, so the stuck run starts at the first such
stop. Only chats updated in the last eight days are read, since Codex keeps
session logs uncompressed for over a week. Chats the limit stopped more than
24 hours before it stopped the latest one were left stuck by an earlier limit
and are skipped. Once the limits read through the CLI allow use, it queues
continue to up to three chats, newest first, and at most three in any five
hours, queueing them together and following them in one loop. When a used-up
limit resets before the next check, the check waits and sends a minute after
the reset, as --reset does. When each chat was last resumed is recorded before queueing in
~/Library/Application Support/codex-resume/recurring.json, and only limit stops
after that count, so each chat is resumed once per stop. An unreadable record
stops the checks rather than risk sending twice, and a lock skips a check while
another runs. It logs only what it sends, when it waits for a reset, and
errors, and does not inhibit sleep. --status lists the stuck chats and what the next check does with each.
--recurring off removes it; --cancel leaves it alone.

A scheduled resume is a user LaunchAgent that starts immediately under
caffeinate -i, so the Mac does not idle-sleep before the run, and waits until
three seconds after the requested local minute. Late starts send immediately
and are logged as late. Completion or failure unloads the job and releases the
sleep assertion. --now does not inhibit sleep, and closing the lid still puts
the Mac to sleep. Scheduled output and errors go to
~/Library/Logs/codex-resume.log, without custom messages; a scheduled message
is stored in the LaunchAgent plist.

--start-window reads the limits when the run starts and sends nothing while a
5-hour window is running or ordinary usage is blocked. A 5-hour window counts
as not running when it is absent, has reset, or reports 0% with a reset a full
window ahead; the live response without a running window is unverified.

--list shows your unarchived chats on this Mac from the Codex app's sidebar
catalogue, newest first, without subagent or review threads. --chat picks one
by id, id prefix, or name: the whole name, part of it, or the starts of its
words in any order; several matches are listed with their last activity
instead of guessing. --project picks a project by its folder name (or full
path) with the same name matching, and uses its most recently active chat; a
scheduled run stores the folder and picks that chat when it starts.
--limits and --reset read the usage limits live through the Codex CLI's app
server (account/rateLimits/read, read-only). --reset schedules a minute after
the latest reset among the limits that are used up. --reset MINUTES also reads
the limits that often while waiting, logging them each time. It sends once no
limit is used up and the limits that were used up when it was scheduled show 0%
used, so an early reset is used too; if those already show use, someone started
on the new allowance elsewhere and it stops without sending. At the reset time
it sends unless that is the case, also when the limits cannot be read.
"""

from datetime import datetime, time as dt_time, timedelta
import argparse
import fcntl
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
from xml.parsers.expat import ExpatError


LABEL = "com.hkjersem.codex-resume"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/codex-resume.log"
RECURRING_LABEL = f"{LABEL}.recurring"
RECURRING_PLIST_PATH = PLIST_PATH.with_name(f"{RECURRING_LABEL}.plist")
RECURRING_STATE_PATH = Path.home() / "Library/Application Support/codex-resume/recurring.json"
RECURRING_CHATS = 3
RECURRING_PERIOD = timedelta(hours=5)
RECURRING_LOOKBACK = timedelta(hours=24)
# Codex keeps session logs uncompressed for over a week, longer than a weekly limit stays used up.
RECURRING_SCAN = timedelta(days=8)
USAGE_LIMIT_ERROR = "usage_limit_exceeded"
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
STATUS_LABEL_WIDTH = 12
# The app checks the queue every ten seconds and sends to a chat once it is not busy and loaded.
QUEUE_PICKUP_SECONDS = 120
# A message still queued this long is in a chat the app has not loaded, so the chat is opened.
QUEUE_OPEN_CHAT_SECONDS = 30
# Opening a chat switches the app to it, so it waits until you have left the app or the Mac this long.
OPEN_CHAT_IDLE_SECONDS = 5 * 60
QUEUE_POLL_SECONDS = 5
TURN_EVENTS = ("task_started", "task_complete", "turn_aborted")
# An unfinished reply this old in the session log is treated as abandoned, as after a crash.
REPLY_STALE_SECONDS = 30 * 60
REPLY_RESULT_SECONDS = 30
# The Codex desktop app, even when named ChatGPT; the separate ChatGPT chat app is com.openai.chat.
APP_BUNDLE_ID = "com.openai.codex"


class AppServerRejectedError(RuntimeError):
    """The app server could not be started or answered with an error, so it did nothing."""


class NotQueuedError(RuntimeError):
    """The message was not queued, so nothing will be sent."""


def next_occurrence(clock_text, now=None):
    match = TIME_PATTERN.fullmatch(clock_text)
    if match is None:
        raise ValueError(
            f"invalid time {clock_text!r}: expected 24-hour HH:MM, HH.MM, or HHMM "
            "(for example, 15:03, 15.03, or 1503)"
        )
    now = now or datetime.now().astimezone()
    hour, minute = (int(part) for part in match.groups())
    # A naive wall-clock time takes the system zone's offset for its own date, which a DST change can differ from.
    scheduled = datetime.combine(now.date(), dt_time(hour, minute)).astimezone()
    if scheduled <= now:
        scheduled = datetime.combine(now.date() + timedelta(days=1), dt_time(hour, minute)).astimezone()
    return scheduled


def validate_message(text):
    message = text.strip()
    if not message:
        raise ValueError("--message must not be empty.")
    if any(unicodedata.category(character) == "Cc" for character in message):
        raise ValueError(
            "--message must be a single line without control characters."
        )
    if message.startswith("/"):
        raise ValueError("--message must not start with '/', which the app reads as a command.")
    return message


def format_time(moment):
    return moment.strftime("%d.%m.%Y kl. %H.%M")


def load_chats():
    # The app's sidebar catalogue: unarchived chats on this Mac, without those on Macs it reaches remotely.
    query = """
        SELECT c.thread_id, c.display_title, c.cwd,
               COALESCE(NULLIF(c.source_recency_at, 0), c.source_updated_at)
        FROM local_thread_catalog c JOIN local_thread_catalog_hosts h USING (host_id)
        WHERE h.host_kind = 'local' AND c.missing_candidate = 0
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
        }
        for chat_id, name, cwd, updated in rows
    ]


def latest_chat():
    chats = load_chats()
    if not chats:
        raise RuntimeError("No Codex chats on this Mac were found.")
    return chats[0]


def describe_chats(chats):
    width = max((len(chat["name"]) for chat in chats), default=0)
    width = min(width, 60)
    lines = []
    for chat in chats:
        name = chat["name"] if len(chat["name"]) <= width else chat["name"][:width - 1] + "…"
        folder = Path(chat["cwd"]).name if chat["cwd"] else ""
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


def resolve_chat(query):
    found = matching_chats(query, load_chats())
    if not found:
        raise RuntimeError(f"No chat matches {query!r}; run codex-resume --list to see your chats.")
    if len(found) > 1:
        raise RuntimeError(
            f"{len(found)} chats match {query!r}; pass --chat with one of these ids:\n"
            + describe_chats(found)
        )
    return found[0]


def project_label(project):
    return f"{project['name']!r}"


def load_projects(chats):
    """Group chats by the folder the app names their project after, newest activity first."""
    projects = {}
    for chat in chats:
        if chat["cwd"]:
            projects.setdefault(chat["cwd"], {
                "name": Path(chat["cwd"]).name, "cwd": chat["cwd"], "chats": [],
            })["chats"].append(chat)
    return list(projects.values())


def resolve_project(query):
    """Return the latest chat in the project matching query, by folder path or project name."""
    found = first_matches(load_projects(load_chats()), (lambda project: project["cwd"] == query.strip(), *name_rules(query)))
    if not found:
        raise RuntimeError(f"No project matches {query!r}; run codex-resume --list to see your chats' projects.")
    if len(found) > 1:
        raise RuntimeError(
            f"{len(found)} projects match {query!r}; pass --project with one of these folders:\n"
            + "\n".join(f"{project['cwd']}  {project_label(project)}" for project in found)
        )
    project = found[0]
    return project, project["chats"][0]


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
    # A reverted chat keeps its id but continues in a new log named <id>_<log id>.
    sessions = CODEX_HOME / "sessions"
    found = [*sessions.glob(f"*/*/*/rollout-*-{chat['id']}.jsonl"),
             *sessions.glob(f"*/*/*/rollout-*-{chat['id']}_*.jsonl")]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def record_time(record):
    """When a session log record was written, or None when its stamp is unreadable or has no time zone."""
    try:
        moment = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def written_since(record, since):
    """Whether a session log record is stamped at or after since; an unreadable stamp counts as older."""
    moment = record_time(record)
    return moment is not None and moment >= since


def turn_events(path, offset=0, since=None):
    """Reply start and end events in a session log, from a byte offset and, if given, a time."""
    return [payload for _, payload in turn_records(path, offset, since)]


def turn_records(path, offset=0, since=None):
    """Reply start and end events with their log records, from a byte offset and, if given, a time."""
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
                events.append((record, payload))
    return events


def replying(chat, now=None):
    """Whether Codex is writing a reply in the chat: its last reply started and has not ended."""
    path = chat_log(chat)
    # A log left unfinished by a crash would otherwise look busy forever.
    if path is None or (now or time.time()) - path.stat().st_mtime > REPLY_STALE_SECONDS:
        return False
    events = turn_events(path)
    return bool(events) and events[-1]["type"] == "task_started"


def reply_result(entry):
    """How the reply a taken message started has ended so far: a line to log, or None while it runs or its
    session log cannot be read yet."""
    path, offset, since = entry["path"], entry["offset"], None
    if path is None or not path.exists():
        # Codex compresses idle chats' logs and unpacks one again before writing to it, so a log found only
        # now holds earlier replies too; only events from after queueing belong to this one.
        path, offset, since = chat_log(entry["chat"]), 0, entry["queued_at"]
        if path is None:
            return None
    try:
        events = turn_events(path, offset, since)
    except OSError:
        return None
    ended = [event for event in events if event["type"] != "task_started"]
    if not ended:
        return None
    error = ended[0].get("error")
    if ended[0]["type"] == "turn_aborted":
        return "The reply was stopped."
    if error:
        detail = error.get("message", error) if isinstance(error, dict) else error
        return f"The reply ended with an error: {detail}"
    return "The reply finished."


def open_app(stamp):
    """Open the Codex app in the background when it is not running, since only the app sends queued messages."""
    try:
        # Prints the app's ASN when it runs, and nothing when it does not.
        running = subprocess.run(["/usr/bin/lsappinfo", "find", f"bundleid={APP_BUNDLE_ID}"],
                                 text=True, capture_output=True, timeout=10)
        if "ASN:" in running.stdout:
            return
        result = subprocess.run(["/usr/bin/open", "-g", "-b", APP_BUNDLE_ID],
                                text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        detail = str(error)
    else:
        if result.returncode == 0:
            print(f"{stamp()}Opened the Codex app in the background.", flush=True)
            return
        detail = (result.stderr or result.stdout).strip()
    print(f"{stamp()}Could not open the Codex app ({detail}); it sends the message once it runs.", flush=True)


def probe(*command):
    return subprocess.run(command, capture_output=True, check=True, timeout=10).stdout


def codex_in_use():
    """Whether you may be using the Codex app: it is frontmost, the screen is unlocked, and you used the Mac
    within OPEN_CHAT_IDLE_SECONDS. When this cannot be read, it counts as in use."""
    try:
        users = plistlib.loads(probe("/usr/sbin/ioreg", "-n", "Root", "-d1", "-a")).get("IOConsoleUsers", [])
        if any(user.get("CGSSessionScreenIsLocked") for user in users):
            return False
        hid = plistlib.loads(probe("/usr/sbin/ioreg", "-r", "-c", "IOHIDSystem", "-d1", "-a"))
        if min(entry["HIDIdleTime"] for entry in hid) / 1e9 >= OPEN_CHAT_IDLE_SECONDS:
            return False
        front = probe("/usr/bin/lsappinfo", "front").decode().strip()
        info = probe("/usr/bin/lsappinfo", "info", "-only", "bundleid", front).decode() if front else ""
    except (OSError, subprocess.SubprocessError, ValueError, ExpatError, KeyError, TypeError, AttributeError):
        return True
    if "CFBundleIdentifier" not in info:
        return True
    return f'"{APP_BUNDLE_ID}"' in info


def open_chat(chat, stamp, announce=True):
    """Open the chat in the background, so the app loads it and sends its queued message, unless you may be
    using the app, since opening switches it to the chat. Returns whether it opened; announce logs why not."""
    if codex_in_use():
        if announce:
            print(f"{stamp()}The app has not taken the message yet and you are using it, so the chat was not "
                  "opened yet; the app sends the message once you open the chat.", flush=True)
        return False
    try:
        result = subprocess.run(["/usr/bin/open", "-g", f"codex://threads/{chat['id']}"],
                                text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        detail = str(error)
    else:
        if result.returncode == 0:
            print(f"{stamp()}The app has not taken the message yet; opened the chat in the background so it "
                  "loads it.", flush=True)
            return True
        detail = (result.stderr or result.stdout).strip()
    if announce:
        print(f"{stamp()}Could not open the chat ({detail}); the app sends the message once you open it.",
              flush=True)
    return False


def resume_through_queue(chat, message, stamp=lambda: ""):
    """Queue the message for the app to send in the chat and follow it; NotQueuedError means nothing was
    queued."""
    entry = queue_message(chat, message, stamp)
    if entry is not None:
        watch_queued([entry])


def queue_message(chat, message, stamp):
    """Queue the message for the app to send in the chat; return what watch_queued follows, or None when there
    is nothing to follow. NotQueuedError means nothing was queued."""
    busy = replying(chat)
    if busy and message is None:
        print(f"{stamp()}Codex is already replying in chat {chat_label(chat)}; not sending '{DEFAULT_MESSAGE}'.",
              flush=True)
        return None
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
        raise NotQueuedError(f"Could not queue the message, so nothing was sent: {error}") from error
    except RuntimeError as error:
        raise RuntimeError(f"The message may have been queued: {error} Check the chat before retrying.") from error
    if busy:
        print(f"{stamp()}Codex is replying in chat {chat_label(chat)}; queued the message, and the app "
              "sends it when the reply ends.", flush=True)
        # A crashed app can leave a reply looking unfinished, so the app may still need opening.
        open_app(stamp)
        return None
    print(f"{stamp()}Queued the message in chat {chat_label(chat)}; waiting for the app to take it.", flush=True)
    open_app(stamp)
    return {"chat": chat, "item": item, "path": path, "offset": offset, "queued_at": queued_at, "stamp": stamp}


def watch_queued(entries):
    """Follow queued messages together: open a chat whose message the app leaves queued, log when the app takes
    each, and then how its reply has ended so far. With several, each line names its chat. A chat whose queue
    cannot be read until the deadline is raised as unknown after the others are followed."""
    def stamp(entry):
        prefix = f"{chat_label(entry['chat'])}: " if len(entries) > 1 else ""
        return lambda: entry["stamp"]() + prefix

    started = time.monotonic()
    waiting, replies, unknown = list(entries), [], []
    while waiting or replies:
        for entry in list(waiting):
            try:
                queued = still_queued(entry["chat"], entry["item"])
                entry["error"] = None
            except RuntimeError as error:
                # Retried on the next round, as the app server may answer then.
                queued, entry["error"] = True, error
            waited = time.monotonic() - started
            if not queued:
                print(f"{stamp(entry)()}The app took the message from the queue.", flush=True)
                waiting.remove(entry)
                replies.append({**entry, "taken": time.monotonic()})
            elif waited >= QUEUE_PICKUP_SECONDS:
                waiting.remove(entry)
                if entry["error"] is not None:
                    unknown.append(f"A message was queued in chat {chat_label(entry['chat'])}, but whether the app "
                                   f"took it is unknown: {entry['error']} Check the chat before retrying.")
                else:
                    print(f"{stamp(entry)()}The app has not taken the message within "
                          f"{QUEUE_PICKUP_SECONDS // 60} minutes; it stays queued, and the app sends it once the "
                          "chat is open.", flush=True)
            elif not entry.get("opened") and waited >= QUEUE_OPEN_CHAT_SECONDS:
                # Tried again each round, as you may stop using the app before the deadline; why it was not
                # opened is logged once.
                entry["opened"] = open_chat(entry["chat"], stamp(entry), announce=not entry.get("tried"))
                entry["tried"] = True
        for entry in list(replies):
            result = reply_result(entry)
            if result is None and time.monotonic() - entry["taken"] < REPLY_RESULT_SECONDS:
                continue
            replies.remove(entry)
            print(f"{stamp(entry)()}{result or 'Codex is replying.'}", flush=True)
        if waiting or replies:
            time.sleep(QUEUE_POLL_SECONDS)
    if unknown:
        raise RuntimeError(" ".join(unknown))


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


def limit_stop(payload):
    error = payload.get("error")
    return (payload["type"] == "task_complete" and isinstance(error, dict)
            and error.get("codex_error_info") == USAGE_LIMIT_ERROR)


def stuck_at_limit(chat, since=None):
    """For a chat whose latest replies, since a time if given, all stopped at a usage limit, when the limit first
    stopped it and when it last did; None when its last reply ended otherwise or is still running."""
    path = chat_log(chat)
    if path is None:
        return None
    stops, running = [], False
    for record, payload in turn_records(path, since=since):
        running = payload["type"] == "task_started"
        if running:
            continue
        # Each message sent while the limit is used up stops at it too, so the chat stays stuck.
        stops = [*stops, (record, payload)] if limit_stop(payload) else []
    if running or not stops or record_time(stops[0][0]) is None or record_time(stops[-1][0]) is None:
        return None
    return {"closed": record_time(stops[0][0]), "stopped": record_time(stops[-1][0])}


def stuck_chats(now=None, resumed=None):
    """Chats on this Mac stuck at a usage limit, newest first, that the limit stopped no more than a day before
    it stopped the latest one, so chats left stuck after an earlier limit are not resumed. For a chat in resumed,
    only stops after it was last resumed count."""
    return [chat for chat in all_stuck_chats(now, resumed) if not chat["earlier"]]


def all_stuck_chats(now=None, resumed=None):
    """As stuck_chats, but also the chats an earlier limit left stuck, marked earlier."""
    now = now or datetime.now().astimezone()
    resumed = resumed or {}
    found = []
    for chat in load_chats():
        if chat["updated"] >= now - RECURRING_SCAN:
            stuck = stuck_at_limit(chat, resumed.get(chat["id"]))
            if stuck is not None:
                found.append({**chat, **stuck})
    if not found:
        return []
    closed = max(chat["closed"] for chat in found)
    return sorted(({**chat, "earlier": chat["closed"] < closed - RECURRING_LOOKBACK} for chat in found),
                  key=lambda chat: chat["stopped"], reverse=True)


def stuck_overview(now, resumed, sends):
    """Each chat stuck at a usage limit, newest first, with what the next recurring check does with it:
    resume, wait for the cap of three resumes in five hours, or skip, as an earlier limit stopped it. A waiting
    chat comes with when the cap next has room for it, or None when that depends on resumes not made yet."""
    recent = sorted(at for at in sends if at >= now - RECURRING_PERIOD)
    free = max(0, RECURRING_CHATS - len(recent))
    waiting = 0
    overview = []
    for chat in all_stuck_chats(now, resumed):
        if chat["earlier"]:
            overview.append((chat, "skip", None))
        elif free:
            free -= 1
            overview.append((chat, "resume", None))
        else:
            # Each waiting chat takes the place of the oldest resume still counted.
            room = recent[waiting] + RECURRING_PERIOD if waiting < len(recent) else None
            waiting += 1
            overview.append((chat, "wait", room))
    return overview


def print_section(title, first=False):
    if not first:
        print()
    heading = f"── {title} ──"
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        heading = f"\033[1m{heading}\033[0m"
    print(heading)


def print_field(label, value):
    print(f"  {label:<{STATUS_LABEL_WIDTH}}{value}")


def print_stuck_overview(state):
    now = local_now()
    resumed = {chat: at for chat, at in state["resumed"].items() if at >= now - RECURRING_SCAN}
    overview = stuck_overview(now, resumed, state["sends"])
    print_section(f"Chats stopped by a usage limit ({len(overview)})")
    if not overview:
        print("  None.")
        return
    rows = []
    for chat, outcome, room in overview:
        if outcome == "resume":
            plan = "next check"
        elif outcome == "skip":
            plan = "left alone"
        else:
            plan = format_time(room.astimezone()) if room is not None else "later"
        rows.append((format_time(chat["stopped"].astimezone()), plan, chat_label(chat)))
    stopped_width = max(len("Stopped"), *(len(row[0]) for row in rows))
    plan_width = max(len("Continues"), *(len(row[1]) for row in rows))
    for row in [("Stopped", "Continues", "Chat"), *rows]:
        print(f"  {row[0]:<{stopped_width}}  {row[1]:<{plan_width}}  {row[2]}")
    if any(outcome == "skip" for _, outcome, _ in overview):
        print("  Chats left alone were stopped by an older usage limit.")


def usage_allowed():
    result = app_server_request("account/rateLimits/read")
    limits = result.get("rateLimits") or {}
    return result.get("ordinaryUsageAllowed") is not False and all(
        (limits.get(key) or {}).get("usedPercent", 0) < 100 for key in ("primary", "secondary"))


def recurring_minutes():
    """The interval of the recurring check, or None when it is not set up."""
    try:
        with RECURRING_PLIST_PATH.open("rb") as agent_file:
            return plistlib.load(agent_file).get("StartInterval", 0) // 60 or None
    except FileNotFoundError:
        return None


def reset_before_next_check(now):
    """A minute after the used-up limit resets, as for --reset, when that comes before the next check."""
    minutes = recurring_minutes()
    if minutes is None:
        return None
    try:
        resets = reset_time(usage_limits())
    except RuntimeError:
        return None
    return resets if resets < now + timedelta(minutes=minutes) else None


def load_recurring_state():
    """The last check, when each chat was last resumed, and every resume within the cap's period; an unreadable
    file is an error, since resuming without it could send twice."""
    try:
        state = json.loads(RECURRING_STATE_PATH.read_text())
    except FileNotFoundError:
        return {"resumed": {}, "sends": []}
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {RECURRING_STATE_PATH} ({error}); fix or delete it") from None
    try:
        resumed = {chat: datetime.fromisoformat(at) for chat, at in state["resumed"].items()}
        # A chat resumed again overwrites its time in resumed, so the cap counts each resume here; records
        # written before this list existed have only resumed to go by.
        sends = ([datetime.fromisoformat(at) for at in state["sends"]] if "sends" in state
                 else list(resumed.values()))
        if any(at.tzinfo is None for at in [*resumed.values(), *sends]):
            raise ValueError("a time has no time zone")
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(f"unexpected contents in {RECURRING_STATE_PATH} ({error}); fix or delete it") from None
    return {**state, "resumed": resumed, "sends": sends}


def save_recurring_state(state):
    temporary = RECURRING_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(
        {**state, "resumed": {chat: at.isoformat() for chat, at in state["resumed"].items()},
         "sends": [at.isoformat() for at in state["sends"]]}, indent=1))
    temporary.replace(RECURRING_STATE_PATH)


def local_now():
    return datetime.now().astimezone()


def recurring_check(now=None):
    """Queue continue to up to three chats stuck at a usage limit once the limits allow it; each chat once per
    limit stop. Returns without checking while another check runs."""
    RECURRING_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECURRING_STATE_PATH.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        check_stuck_chats(now or datetime.now().astimezone())


def check_stuck_chats(now):
    state = load_recurring_state()
    resumed = {chat: at for chat, at in state["resumed"].items() if at >= now - RECURRING_SCAN}
    sends = [at for at in state["sends"] if at >= now - RECURRING_PERIOD]
    state.update(checked=now.isoformat(), resumed=resumed, sends=sends)
    try:
        chats = stuck_chats(now, resumed)
        if not chats:
            return
        if not usage_allowed():
            resets = reset_before_next_check(now)
            if resets is None:
                return
            print(f"{log_stamp()}{len(chats)} chat(s) stopped at a usage limit; waiting until "
                  f"{format_time(resets)}, a minute after it resets.", flush=True)
            wait_until_scheduled_time(resets.isoformat())
            # The wait can run late, as when the Mac slept, and chats can be resumed meanwhile.
            now = local_now()
            chats = stuck_chats(now, resumed)
            if not chats or not usage_allowed():
                return
        recent = sum(at >= now - RECURRING_PERIOD for at in sends)
        # All are queued first and then followed together, so the app takes them within seconds of each other.
        queued = []
        try:
            for chat in chats[:max(0, RECURRING_CHATS - recent)]:
                print(f"{log_stamp()}Chat {chat_label(chat)} stopped at a usage limit "
                      f"{format_time(chat['stopped'].astimezone())}; resuming it.", flush=True)
                # Recorded first, so a message that may have been queued is never sent twice.
                previous = resumed.get(chat["id"])
                resumed[chat["id"]] = now
                sends.append(now)
                save_recurring_state(state)
                try:
                    entry = queue_message(chat, None, log_stamp)
                    if entry is not None:
                        queued.append(entry)
                except NotQueuedError as error:
                    print(f"{log_stamp()}{error}", file=sys.stderr, flush=True)
                    sends.pop()
                    if previous is None:
                        del resumed[chat["id"]]
                    else:
                        resumed[chat["id"]] = previous
        except RuntimeError:
            # The chats queued before the failure still need opening if the app leaves them queued.
            watch_queued(queued)
            raise
        watch_queued(queued)
    finally:
        save_recurring_state(state)


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


def remove_agent(label=LABEL, path=None):
    path = path or PLIST_PATH
    if not path.exists():
        return False
    launchctl("bootout", f"{agent_domain()}/{label}")
    path.unlink()
    return True


def install_agent(plist, path):
    if path.exists():
        remove_agent(plist["Label"], path)
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{plist['Label']}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        plistlib.dump(plist, temporary)
    temporary_path.chmod(0o600)
    temporary_path.replace(path)
    try:
        launchctl("bootstrap", agent_domain(), str(path))
    except RuntimeError:
        path.unlink(missing_ok=True)
        raise


def set_up_recurring(minutes):
    # No caffeinate: a check missed while the Mac sleeps runs when it wakes.
    install_agent({
        "Label": RECURRING_LABEL,
        "ProgramArguments": [sys.executable, str(SCRIPT_PATH), "--recurring-check"],
        "StartInterval": minutes * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }, RECURRING_PLIST_PATH)
    print(recurring_summary(minutes))


def recurring_summary(minutes):
    return (f"Every {minutes} minutes, once the usage limits allow it, up to {RECURRING_CHATS} chats on this Mac "
            "that stopped at a usage limit get continue via the Codex queue.")


def schedule(scheduled, message=None, chat=None, project=None, start_window=False, every=None, used_up=()):
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-i",
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
    install_agent(plist, PLIST_PATH)
    print(scheduled_summary(scheduled, message, None if project is not None else chat, project, start_window,
                            every))


def chat_label(chat):
    # Until the app titles a chat, its catalogue may hold the chat's folder instead.
    name = chat["name"]
    if not name or name.startswith("/"):
        return f"untitled ({Path(chat.get('cwd') or name).name or 'no folder'}, {chat['id'][:8]})"
    return f"{name!r}"


def scheduled_summary(scheduled, message, chat=None, project=None, start_window=False, every=None):
    summary = f"Codex resume scheduled for {format_time(scheduled)}"
    if every:
        summary += f", or earlier if the limit resets first (checked every {every} minutes)"
    if project is not None:
        summary += f" in the latest chat in project {project_label(project)}"
    elif chat is not None:
        summary += f" in chat {chat_label(chat)}"
    else:
        summary += " in the latest chat on this Mac"
    if start_window:
        summary += ", only if no 5-hour window is running"
    return f"{summary} with message: {message}" if message is not None else f"{summary}."


def status():
    minutes = recurring_minutes()
    print_section("Recurring check", first=True)
    if minutes is None:
        print("  Not set up. Start it with --recurring <minutes>.")
    else:
        print_field("Every", f"{minutes} minutes")
        print_field("Sends", f"{DEFAULT_MESSAGE} to up to {RECURRING_CHATS} stopped chats per 5 hours, newest "
                    "first, once the usage limits allow")
        try:
            state = load_recurring_state()
        except RuntimeError as error:
            print_field("Stopped", f"checks stop until this is fixed: {error}")
        else:
            checked = state.get("checked")
            print_field("Last check",
                        format_time(datetime.fromisoformat(checked).astimezone()) if checked else "not yet")
            try:
                print_stuck_overview(state)
            except RuntimeError as error:
                print_section("Chats stopped by a usage limit")
                print(f"  Could not read them: {error}")
    print_section("Scheduled resume")
    if not PLIST_PATH.exists():
        print("  None.")
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
        unknown = {"name": Path(folder).name, "cwd": folder}
        try:
            project = next((project for project in load_projects(load_chats()) if project["cwd"] == folder), unknown)
        except RuntimeError:
            project = unknown
    if "--chat" in arguments:
        chat_id = arguments[arguments.index("--chat") + 1]
        unknown = {"id": chat_id, "name": chat_id}
        try:
            chat = next((chat for chat in load_chats() if chat["id"] == chat_id), unknown)
        except RuntimeError:
            chat = unknown
    every = int(arguments[arguments.index("--every") + 1]) if "--every" in arguments else None
    when = format_time(scheduled)
    if every:
        when += f", or earlier if the limit resets first (checked every {every} minutes)"
    print_field("At", when)
    if project is not None:
        print_field("Chat", f"latest chat in project {project_label(project)}")
    elif chat is not None:
        print_field("Chat", chat_label(chat))
    else:
        print_field("Chat", "latest chat on this Mac")
    print_field("Message", message if message is not None
                else f"{DEFAULT_MESSAGE} (not sent if Codex is already replying)")
    if "--start-window" in arguments:
        print_field("Only if", "no 5-hour window is running")


def log_stamp():
    return f"[{datetime.now():%d.%m.%Y kl. %H.%M.%S}] "


def argument_parser():
    parser = argparse.ArgumentParser(
        prog="codex-resume",
        allow_abbrev=False,
        description=(
            "Send continue, or a message, to a Codex desktop chat on this Mac now, at a set time, "
            "or when a usage limit resets. Times use local 24-hour HH:MM, HH.MM, or HHMM format."
        ),
        epilog=(
            "The message goes through the Codex queue, and the app sends it once the chat is not busy, "
            "also with a locked screen; a draft in the composer is left alone. The app is opened in the "
            "background when it is not running. Without --chat or --project, the chat on this Mac with the "
            "latest activity is used. Scheduled jobs keep the Mac from idle-sleeping while they wait."
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
    actions.add_argument(
        "--recurring", metavar="MINUTES|off",
        help=f"check this often (at least {MIN_RESET_CHECK_MINUTES} minutes) for chats a usage limit stopped, and "
             f"queue continue to up to {RECURRING_CHATS} of them, newest first, once the limits allow it; "
             "'off' stops the checks",
    )
    actions.add_argument("--recurring-check", action="store_true", help=argparse.SUPPRESS)
    actions.add_argument("--status", action="store_true",
                         help="show the scheduled resume, the recurring check and chats stopped at a usage limit")
    actions.add_argument("--cancel", action="store_true", help="cancel the scheduled resume")
    actions.add_argument("--now", action="store_true", help="send now")
    actions.add_argument("--scheduled", metavar="TIMESTAMP", help=argparse.SUPPRESS)
    parser.add_argument("--every", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--used-up", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--message", metavar="TEXT",
        help="with --time, --reset or --now, send this single-line message instead of 'continue'",
    )
    parser.add_argument(
        "--chat", metavar="QUERY",
        help="send to this chat (id, id prefix or name) instead of the latest one; works with --time, "
             "--reset and --now",
    )
    parser.add_argument(
        "--project", metavar="QUERY",
        help="like --chat, but the latest chat in this project (folder name), chosen when the message is sent",
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
            options.time or options.reset is not None or options.now or options.scheduled
        ):
            parser.error(f"{name} requires --time, --reset or --now")
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

    def chosen_chat():
        """The chat to send to; the latest one is only known when the message is sent."""
        return target()[1] or latest_chat()

    if options.list is not None:
        list_chats(options.list)
        return 0
    if options.limits:
        print(describe_limits(usage_limits()))
        return 0
    if options.recurring is not None:
        if options.recurring == "off":
            print("Recurring check stopped." if remove_agent(RECURRING_LABEL, RECURRING_PLIST_PATH)
                  else "No recurring check is set up.")
            return 0
        if not options.recurring.isdigit() or int(options.recurring) < MIN_RESET_CHECK_MINUTES:
            parser.error(f"--recurring takes 'off' or a number of minutes, at least {MIN_RESET_CHECK_MINUTES}")
        set_up_recurring(int(options.recurring))
        return 0
    if options.recurring_check:
        try:
            recurring_check()
        except (OSError, RuntimeError, ValueError) as error:
            print(f"{log_stamp()}Recurring check stopped: {error}", file=sys.stderr, flush=True)
            return 1
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
            # Resolved at run time, so the latest chat is current and a failure still unloads the job.
            resume_through_queue(chosen_chat(), message, log_stamp)
        except (OSError, RuntimeError, ValueError) as error:
            # bootout can terminate us before the outer error handler runs.
            print(f"{log_stamp()}Stopped: {error}", file=sys.stderr, flush=True)
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
        if project is not None:
            print(f"Resuming in chat {chat_label(chat)} in project {project_label(project)}.", flush=True)
        elif chat is not None:
            print(f"Resuming in chat {chat_label(chat)}.", flush=True)
        else:
            chat = latest_chat()
            print(f"Resuming in the latest chat, {chat_label(chat)}.", flush=True)
        resume_through_queue(chat, message)
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
