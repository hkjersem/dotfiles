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
session logs uncompressed for over a week. A chat is left alone after 24 uninterrupted
observed hours with quota and an available scheduling slot but no activity. Any activity,
quota exhaustion, scheduling wait or missed check resets its unattended_since clock.
Once the limits read through the CLI allow use, it queues
continue to up to three chats, newest first, per Codex five-hour usage
window, queueing them together and following them in one loop. Each usage
window takes up to three chats of one tier, the number of ! their title starts
with (read from Codex's thread records, which the catalogue only catches up
with on restart). Lower tiers finish before a higher tier starts its own window, even
when the lower tier needs several windows. Through the app server, the check then sets
each waiting chat's marks to one ! per window it waits, the one it goes in included, so
a chat the next window takes shows one !; its tier is retained until it resumes, and marks
added or removed by hand move it by as many tiers, or to the first when none are left. A
chat the morning holds back past a window that would otherwise take it also gets a ~,
which never affects scheduling; the check records which chats it added ~ to and removes
only those. Resuming a chat removes its marks, and so does no longer stopping at a limit,
as when continued by hand. With --morning HH:MM, nights from ten hours before it split waiting
chats between an early window and one five hours before it. When a used-up
limit resets before the next check, the check waits and sends a minute after
the reset, as --reset does. When each chat was last resumed is recorded before queueing in
~/Library/Application Support/codex-resume/recurring.json, with the resumes and
window counted towards the caps, and only limit stops
after that count, so each chat is resumed once per stop. An unreadable record
stops the checks rather than risk sending twice, and a lock skips a check while
another runs. It logs only what it sends or renames, when it waits for a reset,
and errors, and does not inhibit sleep. --status lists the stuck chats and when each is planned to continue.
A chat whose last reply stopped with a temporary error in RETRY_ERRORS, such as
server_overloaded ("Selected model is at capacity") or a failed connection, gets
continue at the next check once the limits allow use, outside the windows, tiers
and caps, and again after each later such stop, until it has failed for 24
hours. A server error is retried at most three times in a row.
--recurring off removes it; --cancel leaves it alone.

A scheduled resume is a user LaunchAgent that starts immediately under
caffeinate -i, so the Mac does not idle-sleep before the run, and waits until
three seconds after the requested local minute. Late starts send immediately
and are logged as late. Completion or failure unloads the job and releases the
sleep assertion. --now does not inhibit sleep, and closing the lid still puts
the Mac to sleep unless agent-awake is installed: a scheduled run then asks it
to wake the Mac from 90 seconds early and keep it awake until the run ends. Waits
re-read the clock every 30 seconds, so a run starts as soon as the Mac wakes.
Scheduled output and errors go to
~/Library/Logs/codex-resume.log, without custom messages; a scheduled message
is stored in the LaunchAgent plist.

--start-window reads the limits when the run starts and sends nothing while a
5-hour window is running or ordinary usage is blocked. A 5-hour window counts
as not running when it is absent, has reset, or reports 0% with a reset a full
window ahead; the live response without a running window is unverified.

--list shows your unarchived chats on this Mac from the Codex app's sidebar
catalogue, plus new chats it has not caught up with (such as one whose first
turn a usage limit stopped), newest first, without subagent or review threads
or threads without a message. --chat picks one
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

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import agent_awake  # noqa: E402
import output  # noqa: E402
import command_help  # noqa: E402


LABEL = "com.hkjersem.codex-resume"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/codex-resume.log"
RECURRING_LABEL = f"{LABEL}.recurring"
RECURRING_PLIST_PATH = PLIST_PATH.with_name(f"{RECURRING_LABEL}.plist")
RECURRING_STATE_PATH = Path.home() / "Library/Application Support/codex-resume/recurring.json"
RECURRING_CHATS = 3
RECURRING_PERIOD = timedelta(hours=5)
UNATTENDED_LIMIT = timedelta(hours=24)
OBSERVATION_SLACK = timedelta(minutes=1)
# Each leading ! in a chat's title places it in a later tier; the check then sets them to one per window a waiting
# chat waits, and a chat cannot join a window that passed it by.
TIER_MARKER = "!"
# Put before the ! marks of a waiting chat the morning holds back; the check removes only those it added itself,
# and they never affect scheduling.
HOLD_MARKER = "~"
# With --morning, nights run this long before it; a window started this long before it ends by then, and one
# may start up to LATE_SLACK later.
NIGHT_LENGTH = timedelta(hours=10)
LATE_SLACK = timedelta(hours=1)
# How far ahead --status works out when waiting chats continue.
PLAN_HORIZON = timedelta(days=2)
# Codex keeps session logs uncompressed for over a week, longer than a weekly limit stays used up.
RECURRING_SCAN = timedelta(days=8)
# Codex starts a usage window when the app takes the first message, a little after the send is recorded.
WINDOW_START_SLACK = timedelta(minutes=15)
USAGE_LIMIT_ERROR = "usage_limit_exceeded"
# Temporary errors a reply can stop with, after Codex's own retries, and how --status names them; such a chat
# can continue at the next check.
RETRY_ERRORS = {
    "server_overloaded": "model at capacity",
    "rate_limit_exceeded": "rate limited",
    "http_connection_failed": "connection failed",
    "response_stream_connection_failed": "connection failed",
    "response_stream_disconnected": "connection lost",
    "response_too_many_failed_attempts": "too many failed attempts",
    "internal_server_error": "server error",
}
# A server error can recur for one chat whatever the time, so it is only retried this many times in a row.
SERVER_ERROR_RETRIES = 3
SCRIPT_PATH = Path(__file__).resolve()
TIME_PATTERN = re.compile(r"([01][0-9]|2[0-3])[:.]?([0-5][0-9])\Z")
SCHEDULE_DELAY_SECONDS = 3
WAIT_STEP_SECONDS = 30
WAKE_SOURCE = "codex-resume"
RESET_MARGIN_SECONDS = 60
MIN_RESET_CHECK_MINUTES = 5
# A check or send this far past its planned time is logged as late, as after sleep.
LATE_SECONDS = 120
CODEX_HOME = Path.home() / ".codex"
CHAT_CATALOG = "sqlite/codex-dev.db"
CHAT_NAMES = "state_5.sqlite"
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
            catalogued = {chat_id for (chat_id,) in connection.execute("SELECT thread_id FROM local_thread_catalog")}
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError(f"Could not read Codex chats from {path}: {error}") from error
    names = chat_names()
    rows += [row for row in uncatalogued_chats() if row[0] not in catalogued]
    rows.sort(key=lambda row: row[3], reverse=True)
    return [
        {
            "id": chat_id,
            "name": " ".join((names.get(chat_id) or name or "").split()),
            "cwd": cwd,
            "updated": datetime.fromtimestamp(updated).astimezone(),
        }
        for chat_id, name, cwd, updated in rows
    ]


def uncatalogued_chats():
    """Chats on this Mac from Codex's thread records. The app adds a new chat to its catalogue late, and
    not at all while a usage limit stops its first turn, but shows it in the sidebar from the start."""
    # Subagent, guardian and review threads store JSON in source; threads without a message are not shown.
    query = """
        SELECT id, COALESCE(NULLIF(name, ''), title), cwd, COALESCE(NULLIF(recency_at, 0), updated_at)
        FROM threads
        WHERE archived = 0 AND source NOT LIKE '{%'
          AND COALESCE(thread_source, '') NOT IN ('subagent', 'guardian_review')
          AND (COALESCE(name, '') <> '' OR COALESCE(first_user_message, '') <> '')
    """
    path = CODEX_HOME / CHAT_NAMES
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            return connection.execute(query).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []


def chat_names():
    """Chat titles as last set, in the app or through the app server. The app's catalogue shows a title set
    through the app server only after the app restarts; without this record, the catalogue's titles are used."""
    path = CODEX_HOME / CHAT_NAMES
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            return dict(connection.execute("SELECT id, name FROM threads WHERE name <> ''").fetchall())
        finally:
            connection.close()
    except sqlite3.Error:
        return {}


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


def sleep_until(moment, now=None):
    # Short naps re-read the clock, so after a scheduled wake the job does not finish a nap that may not
    # have counted the time the Mac was asleep.
    now = now or datetime.now(moment.tzinfo)
    while (delay := (moment - now).total_seconds()) > 0:
        time.sleep(min(delay, WAIT_STEP_SECONDS))
        now = datetime.now(moment.tzinfo)


def wait_until_scheduled_time(timestamp, now=None):
    sleep_until(datetime.fromisoformat(timestamp) + timedelta(seconds=SCHEDULE_DELAY_SECONDS), now)


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
            sleep_until(next_check)
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


def stop_reason(payload):
    """The codex_error_info a reply ended with, or None; Codex writes some as an object keyed by the reason."""
    error = payload.get("error")
    if payload["type"] != "task_complete" or not isinstance(error, dict):
        return None
    reason = error.get("codex_error_info")
    if isinstance(reason, dict) and len(reason) == 1:
        reason = next(iter(reason))
    return reason if isinstance(reason, str) else None


def limit_stop(payload, reasons=(USAGE_LIMIT_ERROR,)):
    return stop_reason(payload) in reasons


def stuck_at_limit(chat, since=None, reasons=(USAGE_LIMIT_ERROR,)):
    """For a chat whose latest replies, since a time if given, all stopped at a usage limit (or other reasons),
    when it first stopped and when it last did, and the reason of each stop; None when its last reply ended
    otherwise or is still running."""
    path = chat_log(chat)
    if path is None:
        return None
    stops, running = [], False
    for record, payload in turn_records(path, since=since):
        running = payload["type"] == "task_started"
        if running:
            continue
        # Each message sent while the limit is used up stops at it too, so the chat stays stuck.
        stops = [*stops, (record, payload)] if limit_stop(payload, reasons) else []
    if running or not stops or record_time(stops[0][0]) is None or record_time(stops[-1][0]) is None:
        return None
    metadata = path.stat()
    return {"closed": record_time(stops[0][0]), "stopped": record_time(stops[-1][0]),
            "reasons": [stop_reason(payload) for _, payload in stops],
            "revision": f"{path.name}:{metadata.st_mtime_ns}:{metadata.st_size}"}


def stuck_chats(now=None, resumed=None):
    """Chats on this Mac stuck at a usage limit, newest first. For a chat in resumed, only stops after it was
    last resumed count; time waiting for quota or its turn does not make a stop ineligible."""
    now = now or datetime.now().astimezone()
    resumed = resumed or {}
    found = []
    for chat in load_chats():
        if chat["updated"] >= now - RECURRING_SCAN:
            stuck = stuck_at_limit(chat, resumed.get(chat["id"]))
            if stuck is not None:
                found.append({**chat, **stuck})
    return sorted(found, key=lambda chat: chat["stopped"], reverse=True)


def retry_chats(now=None, resumed=None):
    """Chats on this Mac whose last reply stopped with a temporary error, newest first. For a chat in resumed,
    only stops after it was last resumed count. A chat failing for over UNATTENDED_LIMIT, or whose
    SERVER_ERROR_RETRIES retries after a server error all hit one again, is left alone."""
    now = now or datetime.now().astimezone()
    resumed = resumed or {}
    found = []
    for chat in load_chats():
        if chat["updated"] < now - RECURRING_SCAN:
            continue
        stuck = stuck_at_limit(chat, resumed.get(chat["id"]), RETRY_ERRORS)
        if stuck is None:
            continue
        # Retries that fail again extend the run, so the whole run says how long and how often it failed.
        run = stuck_at_limit(chat, None, RETRY_ERRORS) or stuck
        server_errors = 0
        for reason in reversed(run["reasons"]):
            if reason != "internal_server_error":
                break
            server_errors += 1
        # The first server error plus each retry that hit one again.
        if run["closed"] >= now - UNATTENDED_LIMIT and server_errors <= SERVER_ERROR_RETRIES:
            found.append({**chat, **stuck, "reason": stuck["reasons"][-1]})
    return sorted(found, key=lambda chat: chat["stopped"], reverse=True)


def resume_retries(state, now, queued):
    """Queue continue in each chat a temporary error stopped, without waiting for a window or tier, once the
    usage limits allow it; the queued messages are added to queued for following."""
    resumed = state["resumed"]
    chats = retry_chats(now, resumed)
    if not chats or not usage_allowed():
        return
    for chat in chats:
        print(f"{log_stamp()}Chat {chat_label(chat)} stopped with a temporary error ({RETRY_ERRORS[chat['reason']]}) "
              f"{format_time(chat['stopped'].astimezone())}; resuming it.", flush=True)
        # Recorded first, so a message that may have been queued is never sent twice.
        previous = resumed.get(chat["id"])
        resumed[chat["id"]] = now
        save_recurring_state(state)
        try:
            entry = queue_message(chat, None, log_stamp)
        except NotQueuedError as error:
            print(f"{log_stamp()}{error}", file=sys.stderr, flush=True)
            if previous is None:
                del resumed[chat["id"]]
            else:
                resumed[chat["id"]] = previous
            continue
        if entry is not None:
            queued.append(entry)


def title_parts(name):
    """The leading ~ marks, the ! marks after them, and the rest of a title."""
    name = name or ""
    body = name.lstrip(HOLD_MARKER)
    hold = name[:len(name) - len(body)]
    rest = body.lstrip(TIER_MARKER)
    return hold, body[:len(body) - len(rest)], rest


def chat_marks(chat):
    """The ! marks a chat's title starts with, after any ~, and the rest of the title."""
    return title_parts(chat["name"])[1:]


def chat_tier(chat):
    """The chat's tier for this stop, retained when the check removes its marks."""
    return chat.get("resume_tier", len(chat_marks(chat)[0]))


def with_chat_tiers(chats, passed=None, tiers=None):
    passed, tiers = passed or {}, tiers or {}
    result = []
    for chat in chats:
        marks = len(chat_marks(chat)[0])
        previous = tiers.get(chat["id"])
        if previous is not None:
            # Marks added or removed by hand move the tier by as many, as the check's marks count windows; removing
            # them all makes the chat unmarked.
            tier = (previous["tier"] if marks == previous["marks"]
                    else max(0, previous["tier"] + marks - previous["marks"]) if marks else 0)
        else:
            # Older records retain only the last window that removed a mark.
            tier = marks + (chat["id"] in passed)
        result.append({**chat, "resume_tier": chat.get("resume_tier", tier)})
    return result


def remember_chat_tiers(chats):
    return {chat["id"]: {"tier": chat_tier(chat), "marks": len(chat_marks(chat)[0])} for chat in chats}


def night_end(now, morning):
    """With a morning time set, the morning that ends the night now falls in; None by day."""
    if morning is None:
        return None
    end = next_occurrence(morning, now)
    return end if now >= end - NIGHT_LENGTH else None


def window_active(window, now):
    return window is not None and now < window.get("end", window["start"] + RECURRING_PERIOD)


def align_limit_window(window, limit_end, now):
    if limit_end is None or window is None:
        return window
    if limit_end <= now:
        return {**window, "end": limit_end} if window["start"] < limit_end else window
    start = limit_end - RECURRING_PERIOD
    if window.get("end") == limit_end or start - WINDOW_START_SLACK <= window["start"] < limit_end:
        return {**window, "end": limit_end}
    return None


def five_hour_reset(windows):
    for window in windows:
        if window["minutes"] == FIVE_HOUR_MINUTES:
            return window["resets"]
    raise RuntimeError("Codex reported no 5-hour usage window; cannot plan automatic resumes.")


def plan_resumes(chats, now, sends, window, morning=None, passed=None, limit_end=None):
    """Which of the waiting chats, newest first, to resume at now, and the window that then runs; passed holds the
    start of the window that last passed each chat by.

    A Codex usage window holds up to three chats of one retained tier, the lowest waiting; chats of that tier
    that stop meanwhile join it while it has room, unless it passed them by. The reported reset releases all
    slots together. Without a reported future reset, forecasts assume five hours from the first resume.
    At night, a single waiting chat waits for the window that ends
    at morning; two or more are split between an early window, which takes no later chats, and that one, which may
    start up to an hour late.
    """
    window = align_limit_window(window, limit_end, now)
    active = window_active(window, now)
    start = limit_end - RECURRING_PERIOD if limit_end is not None and limit_end > now else None
    if active:
        start = window["start"] if start is None else min(start, window["start"])
    elif start is None:
        start = now
    room = RECURRING_CHATS - sum(start <= at <= now for at in sends)
    if room <= 0 or not chats:
        return [], window
    passed = passed or {}
    if active:
        if window["early"]:
            return [], window
        return [chat for chat in chats
                if chat_tier(chat) == window["tier"] and passed.get(chat["id"]) != window["start"]][:room], window
    tier = min(chat_tier(chat) for chat in chats)
    lowest = [chat for chat in chats if chat_tier(chat) == tier]
    early = False
    end = night_end(now, morning)
    if end is not None:
        late = end - RECURRING_PERIOD
        if now > late + LATE_SLACK:
            return [], window
        if now < late:
            if len(chats) < 2:
                return [], window
            early = True
            # With one tier waiting, the late window gets the larger half.
            if len(lowest) == len(chats):
                lowest = lowest[:len(chats) // 2]
    window = {"start": now, "tier": tier, "early": early}
    if limit_end is not None and limit_end > now:
        window["end"] = limit_end
    return lowest[:room], window


def next_planning_time(now, window, morning, limit_end=None):
    """The next usage-window reset or night boundary, never an individual send's cooldown."""
    moments = [limit_end] if limit_end is not None else []
    if window is not None:
        moments.append(window.get("end", window["start"] + RECURRING_PERIOD))
    if morning is not None:
        end = next_occurrence(morning, now)
        moments += [end - NIGHT_LENGTH, end - RECURRING_PERIOD, end]
    return min((moment for moment in moments if moment > now), default=None)


def pass_by(chats, chosen, window, now, passed):
    """The chats still waiting once chosen are resumed, with the titles of the marked chats the running window
    passes by trimmed, each once per window, to one ! per tier still ahead of them, the running one included, so
    the next tier keeps one; and which window last passed each of them."""
    chosen = {chat["id"] for chat in chosen}
    waiting = [chat for chat in chats if chat["id"] not in chosen]
    passed = {chat["id"]: passed[chat["id"]] for chat in waiting if chat["id"] in passed}
    if not window_active(window, now):
        return waiting, passed
    tiers = {chat_tier(chat) for chat in waiting} | {window["tier"]}
    result = []
    for chat in waiting:
        hold, marks, rest = title_parts(chat["name"])
        ahead = sum(tier < chat_tier(chat) for tier in tiers)
        if (len(marks) > ahead and chat_tier(chat) > window["tier"]
                and passed.get(chat["id"]) != window["start"]):
            chat = {**chat, "name": hold + marks[:ahead] + rest}
            passed[chat["id"]] = window["start"]
        result.append(chat)
    return result, passed


def resume_times(chats, now, sends, window, morning=None, passed=None, start=None, limit_end=None):
    """When each waiting chat continues, by its id, planning from start, now by default, and assuming the limits
    allow it then and no other chat stops; chats that would continue beyond PLAN_HORIZON from now are left out."""
    sends, waiting, passed, times = list(sends), with_chat_tiers(chats, passed), passed or {}, {}
    moment = start or now
    while waiting and moment is not None and moment <= now + PLAN_HORIZON:
        chosen, window = plan_resumes(waiting, moment, sends, window, morning, passed, limit_end)
        times.update((chat["id"], moment) for chat in chosen)
        sends += [moment] * len(chosen)
        waiting, passed = pass_by(waiting, chosen, window, moment, passed)
        moment = next_planning_time(moment, window, morning, limit_end)
    return times


def window_marks(chats, now, sends, window, morning=None, passed=None, start=None, limit_end=None):
    """For each waiting chat not continuing now, by its id: how many windows it waits, the one it goes in
    included, and whether the morning holds it back past a window that would otherwise take it. Chats planned
    beyond PLAN_HORIZON count as going in the window after the last planned."""
    times = resume_times(chats, now, sends, window, morning, passed, start, limit_end)
    # Without the morning, a night's early window is an ordinary one that takes later chats.
    daytime = times if morning is None else resume_times(chats, now, sends, window and {**window, "early": False},
                                                         None, passed, start, limit_end)
    moments = sorted({at for at in times.values() if at > now})
    marks = {}
    for chat in chats:
        at = times.get(chat["id"])
        if at is not None and at <= now:
            continue
        free = daytime.get(chat["id"])
        marks[chat["id"]] = ((moments.index(at) if at is not None else len(moments)) + 1,
                             free is not None and (at is None or free < at))
    return marks


def chat_activity(chat):
    return [chat["stopped"].isoformat(), chat.get("updated", chat["stopped"]).isoformat(),
            chat["name"] or "", chat.get("revision", "")]


def observe_opportunities(state, waiting, now, morning, limit_end, allowed, minutes):
    """Track uninterrupted opportunities, then exclude expired chats without making them block other tiers."""
    chosen, window = plan_resumes(waiting, now, state["sends"], state.get("window"), morning,
                                  state.get("passed"), limit_end)
    checked = datetime.fromisoformat(state["checked"]) if state.get("checked") is not None else None
    continuous = (checked is not None and minutes is not None
                  and 0 <= now.timestamp() - checked.timestamp()
                  <= (timedelta(minutes=minutes) + OBSERVATION_SLACK).total_seconds())
    previous, clocks = state.get("unattended", {}), {}
    if allowed:
        for chat in chosen:
            activity = chat_activity(chat)
            old = previous.get(chat["id"])
            since = (old["since"] if continuous and old and old["activity"] == activity
                     and old["since"] <= now else now)
            clocks[chat["id"]] = {"activity": activity, "since": since}
    expired = {chat for chat, clock in clocks.items()
               if now.timestamp() - clock["since"].timestamp() >= UNATTENDED_LIMIT.total_seconds()}
    waiting = [chat for chat in waiting if chat["id"] not in expired]
    if expired:
        chosen, window = plan_resumes(waiting, now, state["sends"], state.get("window"), morning,
                                      state.get("passed"), limit_end)
        for chat in chosen:
            clocks.setdefault(chat["id"], {"activity": chat_activity(chat), "since": now})
    state.update(unattended=clocks, checked=now.isoformat())
    return waiting, chosen if allowed else [], window, expired


def stuck_overview(now, resumed, sends, window=None, morning=None, passed=None, tiers=None,
                   unattended=None, checked=None, minutes=None):
    """Each chat stuck at a usage limit, newest first, with when it continues: at now, at a later time, or None
    when not within PLAN_HORIZON. Nothing continues before a used-up limit resets."""
    chats = stuck_chats(now, resumed)
    waiting = with_chat_tiers(chats, passed, tiers)
    start, limit_end = None, None
    expired = set()
    if waiting:
        windows = usage_limits()
        limit_end = five_hour_reset(windows)
        window = align_limit_window(window, limit_end, now)
        blocked = any(item["used"] >= 100 for item in windows)
        chosen, _ = plan_resumes(waiting, now, sends, window, morning, passed, limit_end)
        state = {"sends": sends, "window": window, "passed": passed, "checked": checked,
                 "unattended": unattended or {}}
        waiting, _, _, expired = observe_opportunities(
            state, waiting, now, morning, limit_end, bool(chosen) and not blocked and usage_allowed(), minutes)
        if blocked:
            start = reset_time(windows)
    if start is not None and start > now:
        # Checks until then send nothing, but the running window still passes by the marked chats it does not take.
        chosen, _ = plan_resumes(waiting, now, sends, window, morning, passed, limit_end)
        rest, passed = pass_by(waiting, chosen, window, now, passed or {})
        renamed = {chat["id"]: chat for chat in rest}
        waiting = [renamed.get(chat["id"], chat) for chat in waiting]
    else:
        start = None
    times = resume_times(waiting, now, sends, window, morning, passed, start, limit_end)
    return [(chat, "skip" if chat["id"] in expired else "resume" if times.get(chat["id"]) == now else "wait",
             times.get(chat["id"]))
            for chat in chats]


def print_field(label, value):
    print(f"  {label:<{STATUS_LABEL_WIDTH}}{value}")


def print_stuck_overview(state, morning=None):
    now = local_now()
    resumed = {chat: at for chat, at in state["resumed"].items() if at >= now - RECURRING_SCAN}
    overview = stuck_overview(now, resumed, state["sends"], state.get("window"), morning, state.get("passed"),
                              state.get("tiers"), state.get("unattended"), state.get("checked"), recurring_minutes())
    retrying = retry_chats(now, resumed)
    if retrying:
        output.section(f"Chats stopped by a temporary error ({len(retrying)})")
        rows = [(format_time(chat["stopped"].astimezone()), RETRY_ERRORS[chat["reason"]], chat_label(chat))
                for chat in retrying]
        widths = [max(len(heading), *(len(row[index]) for row in rows))
                  for index, heading in enumerate(("Stopped", "Error"))]
        for row in [("Stopped", "Error", "Chat"), *rows]:
            print(f"  {row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]}")
        print("  Each continues at the next check once the usage limits allow it.")
    output.section(f"Chats stopped by a usage limit ({len(overview)})")
    if not overview:
        print("  None.")
        return
    rows = []
    for chat, outcome, at in overview:
        if outcome == "resume":
            plan = "next check"
        elif outcome == "skip":
            plan = "24 hours unattended"
        else:
            plan = format_time(at.astimezone()) if at is not None else "later"
        rows.append((format_time(chat["stopped"].astimezone()), plan, chat_label(chat)))
    stopped_width = max(len("Stopped"), *(len(row[0]) for row in rows))
    plan_width = max(len("Continues"), *(len(row[1]) for row in rows))
    for row in [("Stopped", "Continues", "Chat"), *rows]:
        print(f"  {row[0]:<{stopped_width}}  {row[1]:<{plan_width}}  {row[2]}")


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
        return {"resumed": {}, "sends": [], "window": None, "passed": {}, "tiers": {}, "unattended": {}, "held": []}
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {RECURRING_STATE_PATH} ({error}); fix or delete it") from None
    try:
        resumed = {chat: datetime.fromisoformat(at) for chat, at in state["resumed"].items()}
        # A chat resumed again overwrites its time in resumed, so the cap counts each resume here; records
        # written before this list existed have only resumed to go by.
        sends = ([datetime.fromisoformat(at) for at in state["sends"]] if "sends" in state
                 else list(resumed.values()))
        window = state.get("window")
        if window is not None:
            window = {**window, "start": datetime.fromisoformat(window["start"]), "tier": int(window["tier"]),
                      "early": bool(window["early"])}
            if "end" in window:
                window["end"] = datetime.fromisoformat(window["end"])
        passed = {chat: datetime.fromisoformat(at) for chat, at in state.get("passed", {}).items()}
        tiers = state.get("tiers", {})
        for tier in tiers.values():
            if any(type(tier[key]) is not int or tier[key] < 0 for key in ("tier", "marks")):
                raise ValueError("a chat tier or mark count is not a nonnegative integer")
        held = state.get("held", [])
        if not isinstance(held, list) or any(type(chat) is not str for chat in held):
            raise ValueError("the chats marked with ~ are not a list of chat ids")
        unattended = {}
        for chat, clock in state.get("unattended", {}).items():
            activity = clock["activity"]
            if not isinstance(activity, list) or len(activity) != 4 or any(type(part) is not str for part in activity):
                raise ValueError("a chat activity record is invalid")
            unattended[chat] = {"activity": activity, "since": datetime.fromisoformat(clock["since"])}
        checked = datetime.fromisoformat(state["checked"]) if state.get("checked") is not None else None
        if any(at.tzinfo is None for at in [*resumed.values(), *sends, *passed.values(),
                                            *[clock["since"] for clock in unattended.values()],
                                            *([checked] if checked else []),
                                            *([window["start"]] if window else []),
                                            *([window["end"]] if window and "end" in window else [])]):
            raise ValueError("a time has no time zone")
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(f"unexpected contents in {RECURRING_STATE_PATH} ({error}); fix or delete it") from None
    return {**state, "resumed": resumed, "sends": sends, "window": window, "passed": passed, "tiers": tiers,
            "unattended": unattended, "held": held}


def save_recurring_state(state):
    window = state.get("window")
    temporary = RECURRING_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(
        {**state, "resumed": {chat: at.isoformat() for chat, at in state["resumed"].items()},
         "sends": [at.isoformat() for at in state["sends"]],
         "window": window and {**window, "start": window["start"].isoformat(),
                               **({"end": window["end"].isoformat()} if "end" in window else {})},
         "passed": {chat: at.isoformat() for chat, at in state.get("passed", {}).items()},
         "held": sorted(state.get("held", [])),
         "unattended": {chat: {**clock, "since": clock["since"].isoformat()}
                        for chat, clock in state.get("unattended", {}).items()}}, indent=1))
    temporary.replace(RECURRING_STATE_PATH)


def local_now():
    return datetime.now().astimezone()


def recurring_check(now=None, morning=None):
    """Queue continue to chats stuck at a usage limit once the limits allow it, as plan_resumes decides; each
    chat once per limit stop. Returns without checking while another check runs."""
    RECURRING_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECURRING_STATE_PATH.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        check_stuck_chats(now or datetime.now().astimezone(), morning)


def rename_chat(chat, name, failure):
    """Rename a chat through the app server, reporting a failure without stopping; whether it was renamed."""
    try:
        if not name:
            raise RuntimeError("its title is only marks; rename it in the app")
        app_server_request("thread/name/set", {"threadId": chat["id"], "name": name})
    except RuntimeError as error:
        print(f"{log_stamp()}Could not {failure} chat {chat_label(chat)}: {error}", file=sys.stderr, flush=True)
        return False
    return True


def without_own_hold(chat, held):
    """The chat's title without the ~ marks the check added; all leading ~ count as its own if it added any."""
    name = chat["name"] or ""
    return name.lstrip(HOLD_MARKER) if chat["id"] in held else name


def unmarked_title(chat, held):
    """The title without the ! marks and the ~ marks the check added; a ~ written by hand stays."""
    hold, _, rest = title_parts(without_own_hold(chat, held))
    return hold + rest.strip()


def unmark(chat, held):
    """Remove the ! marks a resumed chat's title starts with, so its next stop counts as unmarked, and any ~ the
    check added."""
    name = unmarked_title(chat, held)
    if name != (chat["name"] or "") and rename_chat(chat, name, "remove the marks from"):
        held.discard(chat["id"])


def release_chats(held, waiting, gone):
    """Remove the ~ the check added from chats no longer waiting, as when continued by hand, and the ! marks from
    the chats in gone, which waited at the last check but are no longer stuck at all."""
    candidates = (held - waiting) | gone
    names = chat_names() if candidates else {}
    if not names:
        return
    for chat_id in candidates:
        chat = {"id": chat_id, "name": " ".join(names.get(chat_id, "").split())}
        name = unmarked_title(chat, held) if chat_id in gone else without_own_hold(chat, held)
        if name == chat["name"]:
            held.discard(chat_id)
        elif rename_chat(chat, name, "remove the marks from"):
            held.discard(chat_id)
            print(f"{log_stamp()}Chat {chat_label(chat)} no longer waits; renamed it "
                  f"{chat_label({**chat, 'name': name})}.", flush=True)


def pass_waiting_chats(state, waiting, chosen, now, forecast=None):
    """Mark each waiting chat with one ! per window it waits, the one it goes in included, and a ~ when the
    morning holds it back, as forecast plans from the morning, start and limit_end it holds. Without a forecast,
    only trim the marks of the chats the running window passes by to the tiers still ahead of them."""
    remaining = [chat for chat in waiting if chat not in chosen]
    after, passed = pass_by(remaining, [], state["window"], now, state["passed"])
    tiers = remember_chat_tiers(
        [chat for chat in waiting if state["resumed"].get(chat["id"]) is None
         or state["resumed"][chat["id"]] < chat["stopped"]])
    held = state["held"]
    planned = {} if forecast is None else window_marks(after, now, state["sends"], state["window"], passed=passed,
                                                       **forecast)
    for before, chat in zip(remaining, after):
        name = without_own_hold(chat, held)
        own = len(chat["name"] or "") - len(name)
        hand, marks, rest = title_parts(name)
        windows, hold = len(marks), own
        if chat["id"] in planned and rest and not rest.startswith("/"):
            windows, morning_hold = planned[chat["id"]]
            hold = int(morning_hold and not hand)
        name = HOLD_MARKER * hold + hand + TIER_MARKER * windows + rest
        trimmed = chat["name"] != before["name"]
        if name == before["name"]:
            if hold:
                held.add(chat["id"])
            else:
                held.discard(chat["id"])
            continue
        renamed = {**chat, "name": name}
        if rename_chat(before, name, "update the marks on"):
            if chat["id"] in tiers:
                tiers[chat["id"]]["marks"] = windows
            if hold:
                held.add(chat["id"])
            else:
                held.discard(chat["id"])
            reason = (f"waits {windows} window{'s' if windows != 1 else ''}"
                      + (", held for the morning" if hold else ""))
            print(f"{log_stamp()}Chat {chat_label(before)} {reason}; renamed it {chat_label(renamed)}.", flush=True)
        elif trimmed:
            del passed[chat["id"]]
    state["passed"] = passed
    state["tiers"] = tiers


def limits_forecast(windows, morning):
    """What the forecast of the next windows needs from the usage limits: the 5-hour reset, and while a limit
    is used up, when it resets."""
    used_up = any(window["used"] >= 100 for window in windows)
    return {"morning": morning, "limit_end": five_hour_reset(windows),
            "start": reset_time(windows) if used_up else None}


def check_stuck_chats(now, morning=None):
    state = load_recurring_state()
    resumed = {chat: at for chat, at in state["resumed"].items() if at >= now - RECURRING_SCAN}
    sends = [at for at in state["sends"] if at >= now - RECURRING_PERIOD]
    state.update(resumed=resumed, sends=sends, held=set(state["held"]))
    previous_window = state["window"]
    # The chats waiting at the last check, so those no longer stuck at all lose their marks.
    previously_waiting = set(state["tiers"])
    # None until the chats are read, so a failed read does not forget which windows passed them by.
    waiting, chats, expired_tiers, queued, forecast = None, [], {}, [], None
    try:
        resume_retries(state, now, queued)
        waiting = with_chat_tiers(stuck_chats(now, resumed), state["passed"], state["tiers"])
        stuck = {chat["id"] for chat in waiting}
        state["tiers"] = remember_chat_tiers(waiting)
        if not waiting:
            state["unattended"] = {}
            return
        forecast = limits_forecast(usage_limits(), morning)
        limit_end = forecast["limit_end"]
        previous_window = align_limit_window(previous_window, limit_end, now)
        state["window"] = previous_window
        chats, window = plan_resumes(waiting, now, sends, previous_window, morning, state["passed"], limit_end)
        allowed = bool(chats) and usage_allowed()
        all_waiting = waiting
        waiting, chats, window, expired = observe_opportunities(
            state, waiting, now, morning, limit_end, allowed, recurring_minutes())
        expired_tiers = remember_chat_tiers([chat for chat in all_waiting if chat["id"] in expired])
        if not chats:
            resets = reset_before_next_check(now)
            if resets is None:
                return
            print(f"{log_stamp()}{len(chats) or len(waiting)} chat(s) stopped at a usage limit; waiting until "
                  f"{format_time(resets)}, a minute after it resets.", flush=True)
            # Chats resumed after a temporary error are followed now rather than after the wait.
            watch_queued(queued)
            queued.clear()
            wait_until_scheduled_time(resets.isoformat())
            # The wait can run late, as when the Mac slept, and chats can be resumed meanwhile.
            now = local_now()
            waiting = with_chat_tiers(stuck_chats(now, resumed), state["passed"], state["tiers"])
            stuck = {chat["id"] for chat in waiting}
            state["tiers"] = remember_chat_tiers(waiting)
            # A failed read leaves the ~ marks alone rather than forecasting from the limits before the wait.
            forecast = None
            forecast = limits_forecast(usage_limits(), morning)
            limit_end = forecast["limit_end"]
            previous_window = align_limit_window(previous_window, limit_end, now)
            state["window"] = previous_window
            chats, window = plan_resumes(waiting, now, sends, previous_window, morning, state["passed"], limit_end)
            allowed = bool(chats) and usage_allowed()
            all_waiting = waiting
            waiting, chats, window, expired = observe_opportunities(
                state, waiting, now, morning, limit_end, allowed, recurring_minutes())
            expired_tiers = remember_chat_tiers([chat for chat in all_waiting if chat["id"] in expired])
            if not chats:
                return
        # All are queued first and then followed together, so the app takes them within seconds of each other.
        for chat in chats:
            print(f"{log_stamp()}Chat {chat_label(chat)} stopped at a usage limit "
                  f"{format_time(chat['stopped'].astimezone())}; resuming it.", flush=True)
            # Recorded first, so a message that may have been queued is never sent twice.
            previous = resumed.get(chat["id"])
            resumed[chat["id"]] = now
            sends.append(now)
            state["window"] = window
            save_recurring_state(state)
            not_queued = False
            try:
                entry = queue_message(chat, None, log_stamp)
            except NotQueuedError as error:
                not_queued = True
                print(f"{log_stamp()}{error}", file=sys.stderr, flush=True)
                entry = None
                if previous is None:
                    del resumed[chat["id"]]
                else:
                    resumed[chat["id"]] = previous
            if not not_queued:
                state["unattended"].pop(chat["id"], None)
            if entry is None:
                # Nothing was sent, either because queueing failed or because Codex is already replying in
                # the chat; a replying chat stays recorded, so its earlier stop is not resumed later.
                sends.pop()
                # A window none of its chats were resumed in has not started.
                if not any(at >= window["start"] for at in sends):
                    state["window"] = previous_window
                continue
            queued.append(entry)
            unmark(chat, state["held"])
    finally:
        if sys.exc_info()[0] is not None:
            state["unattended"] = {}
        state["checked"] = now.isoformat()
        if waiting is not None:
            pass_waiting_chats(state, waiting, chats, now, forecast)
            state["tiers"].update(expired_tiers)
            release_chats(state["held"], {chat["id"] for chat in waiting}, previously_waiting - stuck)
        save_recurring_state(state)
        # Also after a failure, as the chats queued before it still need opening if the app leaves them queued.
        watch_queued(queued)


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


def set_up_recurring(minutes, morning=None):
    if RECURRING_STATE_PATH.exists():
        with RECURRING_STATE_PATH.with_suffix(".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("a recurring check is running; retry changing its setup when it finishes") from None
            state = load_recurring_state()
            state["unattended"] = {}
            save_recurring_state(state)
    # No caffeinate: a check missed while the Mac sleeps runs when it wakes.
    install_agent({
        "Label": RECURRING_LABEL,
        "ProgramArguments": [sys.executable, str(SCRIPT_PATH), "--recurring-check",
                             *(["--morning", morning] if morning else [])],
        "StartInterval": minutes * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }, RECURRING_PLIST_PATH)
    print(recurring_summary(minutes))
    if morning:
        print(night_summary(morning))


def recurring_summary(minutes):
    return (f"Every {minutes} minutes, once the usage limits allow it, up to {RECURRING_CHATS} chats on this Mac "
            "that stopped at a usage limit get continue via the Codex queue. Chats whose title starts with "
            f"{TIER_MARKER} go in later tiers, one tier per 5-hour window. Waiting chats then show one "
            f"{TIER_MARKER} per window they wait without changing their tier, and a {HOLD_MARKER} when the "
            "morning holds them back. Chats a temporary error, such as the model being at capacity, stopped "
            "continue at the next check.")


def night_summary(morning):
    end = next_occurrence(morning)
    return (f"At night, from {end - NIGHT_LENGTH:%H.%M}, waiting chats are split between an early window and one "
            f"from {end - RECURRING_PERIOD:%H.%M}, and a single chat waits for that one, so a new window is free "
            f"at about {end:%H.%M}.")


def recurring_morning():
    """The morning time the recurring check plans nights around, or None."""
    try:
        with RECURRING_PLIST_PATH.open("rb") as agent_file:
            arguments = plistlib.load(agent_file).get("ProgramArguments", [])
    except FileNotFoundError:
        return None
    return arguments[arguments.index("--morning") + 1] if "--morning" in arguments else None


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
    woken = agent_awake.request_wake(WAKE_SOURCE, scheduled) is not None
    print(scheduled_summary(scheduled, message, None if project is not None else chat, project, start_window,
                            every))
    if woken:
        print("agent-awake wakes the Mac for it, also with the lid closed.")


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
    output.section("Recurring check")
    if minutes is None:
        print("  Not set up. Start it with --recurring <minutes>.")
    else:
        print_field("Every", f"{minutes} minutes")
        print_field("Sends", f"{DEFAULT_MESSAGE} to up to {RECURRING_CHATS} stopped chats per Codex usage window, newest "
                    f"first, once the usage limits allow; one tier per window, with each leading "
                    f"{TIER_MARKER} placing a chat in a later tier")
        morning = recurring_morning()
        if morning:
            print_field("Mornings", f"{morning}. {night_summary(morning)}")
        try:
            state = load_recurring_state()
        except RuntimeError as error:
            print_field("Stopped", f"checks stop until this is fixed: {error}")
        else:
            checked = state.get("checked")
            print_field("Last check",
                        format_time(datetime.fromisoformat(checked).astimezone()) if checked else "not yet")
            try:
                print_stuck_overview(state, morning)
            except RuntimeError as error:
                output.section("Chats stopped by a usage limit")
                print(f"  Could not read them: {error}")
    output.section("Scheduled resume")
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
        description=command_help.description("codex-resume") +
                    "\n\nTimes use local 24-hour HH:MM, HH.MM, or HHMM format.",
        epilog=command_help.help_epilog("codex-resume",
            "The message goes through the Codex queue, and the app sends it once the chat is not busy, "
            "also with a locked screen; a draft in the composer is left alone. The app is opened in the "
            "background when it is not running. Without --chat or --project, the chat on this Mac with the "
            "latest activity is used. Scheduled jobs keep the Mac from idle-sleeping while they wait."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
             "chats a temporary error stopped continue at the next check; 'off' stops the checks",
    )
    actions.add_argument("--recurring-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--morning", metavar="HH:MM",
        help="with --recurring, plan nights around this time: from ten hours before it, chats go in an early "
             "window and one starting five hours before it, so a new window is free then",
    )
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
    if options.morning is not None:
        if not (options.recurring not in (None, "off") or options.recurring_check):
            parser.error("--morning requires --recurring MINUTES")
        match = TIME_PATTERN.fullmatch(options.morning)
        if match is None:
            parser.error(f"invalid --morning time {options.morning!r}: expected 24-hour HH:MM, HH.MM, or HHMM")
        options.morning = ":".join(match.groups())
    if options.recurring is not None:
        if options.recurring == "off":
            print("Recurring check stopped." if remove_agent(RECURRING_LABEL, RECURRING_PLIST_PATH)
                  else "No recurring check is set up.")
            return 0
        if not options.recurring.isdigit() or int(options.recurring) < MIN_RESET_CHECK_MINUTES:
            parser.error(f"--recurring takes 'off' or a number of minutes, at least {MIN_RESET_CHECK_MINUTES}")
        set_up_recurring(int(options.recurring), options.morning)
        return 0
    if options.recurring_check:
        try:
            recurring_check(morning=options.morning)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"{log_stamp()}Recurring check stopped: {error}", file=sys.stderr, flush=True)
            return 1
        return 0
    if options.status:
        status()
        return 0
    if options.cancel:
        agent_awake.cancel_wakes(WAKE_SOURCE)
        if remove_agent():
            print(f"  {output.mark('ok')} Scheduled Codex resume cancelled.")
        else:
            output.note("No Codex resume is scheduled.")
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
            agent_awake.cancel_wakes(WAKE_SOURCE)
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
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
