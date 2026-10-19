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
control, preserving drafts or inserting continue when empty. --message replaces
continue with a single-line custom message. That message is typed only into a
recognised, empty composer: a draft or an unrecognised composer stops before any
input, and such stops are retryable when scheduled. This app exposes
its placeholder (sometimes with a newline) as AXValue when empty. A placeholder
match ignoring surrounding whitespace is treated as empty, even if deliberately
typed. If no composer label is recognised, it activates the app and sends only
Return to its existing focus, without typing text. This fallback cannot verify
what has focus or whether a message was sent, and never retries after Return.
Unreadable or ambiguous controls still fail; they do not use the fallback.

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
"""

from datetime import datetime, timedelta
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata


LABEL = "com.hkjersem.codex-resume"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library/Logs/codex-resume.log"
SCRIPT_PATH = Path(__file__).resolve()
TIME_PATTERN = re.compile(r"([01][0-9]|2[0-3])[:.]?([0-5][0-9])\Z")
SCHEDULE_DELAY_SECONDS = 3
RETRY_INTERVAL_SECONDS = 60
MAX_SCHEDULED_RETRIES = 10
HELPER_PATH = SCRIPT_PATH.parent / "lib/codex-resume.swift"
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


def schedule(clock_text, message=None):
    scheduled = next_occurrence(clock_text)
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

    print(scheduled_summary(scheduled, message))


def scheduled_summary(scheduled, message):
    summary = f"Codex resume scheduled for {scheduled.strftime('%Y-%m-%d %H:%M')}"
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
    print(scheduled_summary(scheduled, message))


def swift_version(executable):
    try:
        result = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = SWIFT_VERSION_PATTERN.search(result.stdout + result.stderr)
    return tuple(int(part) for part in match.groups()) if match else None


def run_automation(action, message=None):
    executable = shutil.which("swift")
    if executable is None:
        raise RuntimeError(
            "Swift is unavailable; install Apple's Xcode Command Line Tools."
        )
    command = [executable, str(HELPER_PATH), action]
    if message is not None:
        command += ["--message", message]
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
    if result.returncode == 4 and action in (
        "--scheduled-initial-submit", "--scheduled-submit",
    ):
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
    print(result.stdout, end="", flush=True)


def submit(action="--submit", message=None):
    run_automation(action, message)


def submit_scheduled(message=None):
    for attempt in range(MAX_SCHEDULED_RETRIES + 1):
        started = time.monotonic()
        print(
            f"[{datetime.now():%d.%m.%Y kl. %H.%M.%S}] "
            f"Scheduled Codex submission attempt {attempt + 1}/"
            f"{MAX_SCHEDULED_RETRIES + 1}.",
            flush=True,
        )
        try:
            submit(
                "--scheduled-initial-submit" if attempt == 0 else "--scheduled-submit",
                message,
            )
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
        help="with --time or --now, type this single-line message instead of 'continue'",
    )
    return parser


def main(arguments):
    parser = argument_parser()
    options = parser.parse_args(arguments)
    message = None
    if options.message is not None:
        if not (options.time or options.now or options.scheduled):
            parser.error("--message requires --time or --now")
        message = validate_message(options.message)
    if options.status:
        status()
        return 0
    if options.cancel:
        if remove_agent():
            print("Scheduled Codex resume cancelled.")
        else:
            print("No Codex resume is scheduled.")
        return 0
    if options.now:
        submit(message=message)
        return 0
    if options.inspect:
        run_automation("--inspect")
        return 0
    if options.check:
        run_automation("--check")
        return 0
    if options.scheduled:
        try:
            wait_until_scheduled_time(options.scheduled)
            submit_scheduled(message)
        except (OSError, RuntimeError, ValueError) as error:
            # bootout can terminate us before the outer error handler runs.
            print(f"codex-resume: {error}", file=sys.stderr, flush=True)
            return 1
        finally:
            if PLIST_PATH.exists():
                PLIST_PATH.unlink()
                launchctl("bootout", f"{agent_domain()}/{LABEL}")
        return 0
    schedule(options.time, message)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"codex-resume: {error}", file=sys.stderr)
        sys.exit(1)
