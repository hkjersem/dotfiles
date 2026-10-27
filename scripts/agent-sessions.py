#!/usr/bin/env python3
"""Read-only report of recorded terminal-session restoration candidates."""

import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import agent_sessions  # noqa: E402
import output  # noqa: E402


UUID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
HELP = """Usage: agent-sessions [--help]
Show recorded Copilot and Claude terminal sessions, their titles and restore status.
This only reads state; it does not resume, register or clean up sessions.

  -h, --help  Show this help
"""


class Session(NamedTuple):
    project: str
    session: str
    title: str
    launcher: str
    shell: str
    status: str
    app: str = ""


def process_starts():
    result = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "lstart="],
        capture_output=True, text=True, check=True, env={**os.environ, "LC_ALL": "C"},
    )
    return {parts[0]: " ".join(parts[1:]) for line in result.stdout.splitlines()
            if len(parts := line.split()) == 6}


def read_optional(path):
    try:
        return path.read_text().splitlines(), path.stat().st_mtime_ns
    except FileNotFoundError:
        # A running agent can finish or atomically replace an observation while we read.
        return None, 0


def read_session(marker, starts, now):
    lines, activity = read_optional(marker)
    if lines is None:
        return None
    name, _, claim = marker.name.partition(".")
    if len(lines) not in (7, 8):
        return Session(name, "", "", "", "", "Invalid record")
    cwd, launcher, _, pid, started, initial, agent = lines[:7]
    app = lines[7] if len(lines) == 8 else ""
    if not cwd or launcher not in ("copilot", "claude", "cplt") \
            or agent not in ("copilot", "claude") or not pid.isdigit():
        return Session(name, "", "", launcher, pid, "Invalid record")
    session = initial if re.fullmatch(UUID, initial) else ""
    observations = []
    for path in (marker.parent / f"{name}.current", Path.home() / ".cache/agent-sessions" / name):
        content, stamp = read_optional(path)
        activity = max(activity, stamp)
        if content and re.fullmatch(UUID, content[0]):
            observations.append((stamp, content[0]))
    if observations:
        # Stable sorting preserves .current on a timestamp tie, as the wrapper does.
        observations.sort(key=lambda item: item[0], reverse=True)
        session = observations[0][1]
    live = pid in starts and (not started or starts[pid] == " ".join(started.split()))
    history = False
    if session:
        home = agent_sessions.agent_home(agent)
        history = ((home / "session-state" / session / "events.jsonl").is_file() if agent == "copilot"
                   else any((home / "projects").glob(f"*/{session}.jsonl")))
    if claim:
        status = "Restore claimed" if claim in starts else "Stale restore claim"
    elif live:
        status = "Live, tracked" if history else "Live, history missing" if session else "Live, session unknown"
    elif int(now) - activity // 1_000_000_000 >= 7 * 86400:
        status = "Expired"
    elif not session:
        status = "Session unknown"
    else:
        status = "Waiting to restore" if history else "History missing"
    return Session(cwd, session, agent_sessions.session_name(agent, session) or "",
                   f"{launcher}/{agent}" if launcher == "cplt" else launcher, pid, status, app)


def sessions(directory):
    try:
        markers = sorted(path for path in directory.iterdir()
                         if re.fullmatch(UUID + r"(?:\.[0-9]+)?", path.name) and path.is_file())
    except FileNotFoundError:
        return []
    if not markers:
        return []
    starts, now = process_starts(), time.time()
    result = [read_session(path, starts, now) for path in markers]
    return sorted((entry for entry in result if entry is not None),
                  key=lambda entry: (entry.project, entry.session, entry.shell))


def display_path(path):
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return path


def cell(value):
    return "".join(char if char.isprintable() else " " for char in value)


def report(directory, entries):
    output.context(display_path(str(directory)), f"{len(entries)} recorded sessions")
    if not entries:
        output.section("Sessions")
        print(f"  {output.mark('info')} No recorded sessions.")
        return
    headers = ("Project", "Session", "Title", "App", "Launcher", "Shell", "Status")
    rows = []
    for entry in entries:
        title = cell(entry.title) or "(untitled)"
        if len(title) > 44:
            title = title[:41] + "..."
        rows.append((cell(display_path(entry.project)), entry.session[:8] or "-",
                     title, cell({"vscode": "VS Code", "iTerm.app": "iTerm",
                                  "Apple_Terminal": "Terminal"}.get(entry.app, entry.app)) or "Unknown",
                     cell(entry.launcher) or "-", cell(entry.shell) or "-", entry.status))
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]
    format = "  ".join(f"%-{width}s" for width in widths[:-1]) + "  %s"
    groups = {"Live sessions": [], "Waiting to restore": [], "Other records": []}
    for entry, row in zip(entries, rows):
        group = ("Waiting to restore" if entry.status == "Waiting to restore"
                 else "Live sessions" if entry.status.startswith("Live,") else "Other records")
        groups[group].append(row)
    for name, group in groups.items():
        if not group:
            continue
        output.section(name)
        output.table_header(format, *headers)
        for row in group:
            print("  " + format % row)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if "-h" in args or "--help" in args:
        print(HELP, end="")
        return 0
    if args:
        print(f"Error: unknown argument: {args[0]}", file=sys.stderr)
        return 2
    directory = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "agent-sessions"
    try:
        entries = sessions(directory)
        report(directory, entries)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f"Error: could not read session restoration status: {error}", file=sys.stderr)
        return 1
    return int(any(entry.status == "Invalid record" for entry in entries))


if __name__ == "__main__":
    sys.exit(main())
