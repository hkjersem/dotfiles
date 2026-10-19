#!/usr/bin/env python3
"""Schedule a one-time submission through Codex's Accessibility controls.

Requires macOS, Python 3, Swift (Xcode Command Line Tools), and Accessibility
permission for the execution context. Terminal permission may not cover launchd.

The user LaunchAgent starts immediately under caffeinate -di, waits until three
seconds after the requested local minute, then runs lib/codex-resume.swift.
Late starts submit immediately. Completion, failure, or cancellation unloads
the job and releases the sleep assertions. --now does not inhibit idle sleep.
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
custom message. That message is typed only into a recognised, empty composer: a
draft or an unrecognised composer stops before any input, retryably when
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
the latest reset among the limits that are used up.
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


LABEL = "com.hkjersem.codex-resume"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/codex-resume.log"
SCRIPT_PATH = Path(__file__).resolve()
TIME_PATTERN = re.compile(r"([01][0-9]|2[0-3])[:.]?([0-5][0-9])\Z")
SCHEDULE_DELAY_SECONDS = 3
RESET_MARGIN_SECONDS = 60
CODEX_HOME = Path.home() / ".codex"
CHAT_CATALOG = "sqlite/codex-dev.db"
CODEX_CLI_PATHS = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
)
APP_SERVER_TIMEOUT_SECONDS = 20
LIST_LIMIT = 20
LIMIT_NAMES = {300: "5-hour", 10080: "weekly"}
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
        raise RuntimeError("The Codex CLI is unavailable, so the usage limits cannot be read.")
    return found


def app_server_request(method):
    """Send one JSON-RPC request to a short-lived app server and return its result."""
    process = subprocess.Popen(
        [codex_cli(), "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
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
        # The server exits at end of input before answering, so stdin stays open until the reply.
        for request in (
            {"id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "codex-resume", "version": "1"}}},
            {"method": "initialized"},
            {"id": 2, "method": method},
        ):
            process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        if not received.wait(APP_SERVER_TIMEOUT_SECONDS):
            raise RuntimeError(f"The Codex app server did not answer {method} in time.")
    finally:
        process.kill()
        process.wait()
    reply = replies.get("reply")
    if reply is None:
        raise RuntimeError(f"The Codex app server exited without answering {method}.")
    if "error" in reply:
        raise RuntimeError(f"The Codex app server rejected {method}: {reply['error'].get('message', reply['error'])}")
    return reply["result"]


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
            "used": window.get("usedPercent", 0),
            "resets": datetime.fromtimestamp(window["resetsAt"]).astimezone(),
        })
    if not windows:
        raise RuntimeError("Codex reported no usage limits.")
    return windows


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


def wait_until_scheduled_time(timestamp, now=None):
    scheduled = datetime.fromisoformat(timestamp) + timedelta(seconds=SCHEDULE_DELAY_SECONDS)
    now = now or datetime.now(scheduled.tzinfo)
    delay = (scheduled - now).total_seconds()
    if delay > 0:
        time.sleep(delay)


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


def schedule(scheduled, message=None, chat=None, project=None):
    if PLIST_PATH.exists():
        remove_agent()

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-di",
            sys.executable,
            str(SCRIPT_PATH),
            *(["--message", message] if message is not None else []),
            # A project keeps its folder, so the latest chat is chosen when the run starts.
            *(["--project", project["cwd"]] if project is not None
              else ["--chat", chat["id"]] if chat is not None else []),
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

    print(scheduled_summary(scheduled, message, None if project is not None else chat, project))


def chat_label(chat):
    return f"{chat['name']!r}" + (" (remote)" if chat["host"] is not None else "")


def scheduled_summary(scheduled, message, chat=None, project=None):
    summary = f"Codex resume scheduled for {format_time(scheduled)}"
    if project is not None:
        summary += f" in the latest chat in project {project_label(project)}"
    elif chat is not None:
        summary += f" in chat {chat_label(chat)}"
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
    print(scheduled_summary(scheduled, message, chat, project))


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


def submit_scheduled(message=None, chat=None):
    for attempt in range(MAX_SCHEDULED_RETRIES + 1):
        started = time.monotonic()
        print(
            f"[{datetime.now():%d.%m.%Y kl. %H.%M.%S}] "
            f"Scheduled Codex submission attempt {attempt + 1}/"
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
            "Submission activates the app and sends Return, verifying composer focus "
            "when recognised. If unrecognised, it sends only Return to the app's "
            "existing focus, without typing text or retrying after Return. "
            "Inspection does not activate the app. "
            "An empty composer is filled with 'continue', or with --message; existing "
            "drafts are preserved, and --message never adds to or sends a draft. "
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
        "--reset", action="store_true",
        help="schedule a minute after the used-up usage limit resets (the later one if both are)",
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
    return parser


def main(arguments):
    parser = argument_parser()
    options = parser.parse_args(arguments)
    message = None
    if options.message is not None:
        if not (options.time or options.reset or options.now or options.scheduled):
            parser.error("--message requires --time, --reset or --now")
        message = validate_message(options.message)
    for name, value in (("--chat", options.chat), ("--project", options.project)):
        if value is not None and not (
            options.time or options.reset or options.now or options.check
            or options.inspect or options.scheduled
        ):
            parser.error(f"{name} requires --time, --reset, --now, --check or --inspect")
    if options.chat is not None and options.project is not None:
        parser.error("use either --chat or --project, not both")

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
            wait_until_scheduled_time(options.scheduled)
            # Resolved at run time, so a project gets its latest chat and a failure still unloads the job.
            _, chat = target()
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
        if chat is not None:
            where = f" in project {project_label(project)}" if project is not None else ""
            print(f"Resuming in chat {chat_label(chat)}{where}.", flush=True)
        submit(message=message, chat=chat)
        return 0
    if options.inspect:
        run_automation("--inspect", chat=chat)
        return 0
    if options.check:
        run_automation("--check", chat=chat)
        return 0
    if options.reset:
        windows = usage_limits()
        scheduled = reset_time(windows)
        print(describe_limits(windows))
    else:
        scheduled = next_occurrence(options.time)
    schedule(scheduled, message, chat, project)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"codex-resume: {error}", file=sys.stderr)
        sys.exit(1)
