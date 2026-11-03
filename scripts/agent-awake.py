#!/usr/bin/env python3
"""Keep this Mac awake, also with the lid closed, while Copilot CLI, Claude Code or Codex is working on a
turn, and let it sleep again when they are done.

Each agent gets hooks that run this script with --hook: a submitted prompt starts a hold, each tool call
marks it busy when it starts and refreshes it when it ends, and the end of the turn or a permission prompt
releases it. Each started subagent adds a hold of its own, released when a subagent stops; an interrupt or
the end of the session releases them all. A hold is a file in ~/.cache/agent-awake/holds holding the
agent's process ID. Open sessions that are not working hold nothing.

A root LaunchDaemon, scripts/agent-awake-daemon.sh installed as a root-owned copy, reads those files. While
one is active it sets pmset disablesleep, the only switch that keeps a closed MacBook awake. A hold counts
until its agent process exits, its turn is released (plus 45 seconds, so a turn that continues does not
let the Mac sleep in between), or no tool call has refreshed it for 20 minutes, or two hours while a
tool call is running, such as a long build. When none is left it clears
disablesleep and, with the lid closed and no external display, puts the Mac to sleep. On battery below
the --battery charge it lets the Mac sleep anyway.

Wake requests in ~/.cache/agent-awake/wake, such as codex-resume's scheduled runs, make the helper
schedule wakes with pmset from 90 seconds early and keep the Mac awake until the request is withdrawn, or at most
ten minutes past its time.

Installing needs an administrator password once. Run it only on Macs that need it: nothing installs it
automatically.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import agent_awake  # noqa: E402
from agent_sessions import json_lines_from_end, session_name  # noqa: E402
import output  # noqa: E402
import command_help  # noqa: E402

DAEMON_SCRIPT = Path(__file__).resolve().parent / "agent-awake-daemon.sh"
HOOK_MARK = re.compile(r"/agent-awake\.py\"? --hook ")
HOOK_TIMEOUT_SECONDS = 10
DEFAULT_BATTERY = 20
# Hold files this old are left over from crashed sessions; the helper ignores them long before this.
STALE_HOLD_SECONDS = 24 * 3600
# Mirrors the helper's LEASE_SECONDS, BUSY_SECONDS and RELEASE_GRACE_SECONDS, for --status.
LEASE_SECONDS = 1200
BUSY_SECONDS = 7200
RELEASE_GRACE_SECONDS = 45
RECENT_HOLDS = 5
KIND_ORDER = {"ok": 0, "warn": 1, "info": 2}
# Subagent holds from before they were named after the subagent: <time_ns>-<pid>.
UNNAMED_SUBAGENT = re.compile(r"\d{19}-\d+$")
NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")
LOG_LINES = 5

# (event, matcher, action) for each agent. Copilot's camelCase events give camelCase payload fields; the
# others use snake_case. A subagent gets a hold of its own, named after its agentId or agent_id when the
# payload has one. Copilot runs a subagent's tool hooks with that ID as their sessionId; Claude Code passes
# the parent's session_id, so its tool calls refresh the parent and all of its subagents.
AGENTS = {
    "copilot": {
        "label": "Copilot CLI",
        "home": ".copilot",
        "file": ".copilot/hooks/agent-awake.json",
        "processes": ("copilot",),
        "events": (
            ("userPromptSubmitted", None, "start"),
            ("preToolUse", None, "busy"),
            ("postToolUse", None, "refresh"),
            ("subagentStart", None, "substart"),
            ("subagentStop", None, "substop"),
            ("agentStop", None, "stop"),
            ("errorOccurred", None, "error"),
            ("sessionEnd", None, "end"),
            ("notification", "permission_prompt|elicitation_dialog", "stop"),
        ),
    },
    "claude": {
        "label": "Claude Code",
        "home": ".claude",
        "file": ".claude/settings.json",
        "processes": ("claude",),
        "events": (
            ("UserPromptSubmit", None, "start"),
            ("PreToolUse", None, "busy"),
            ("PostToolUse", None, "refresh"),
            ("PostToolUseFailure", None, "refresh"),
            ("SubagentStart", None, "substart"),
            ("SubagentStop", None, "substop"),
            ("Stop", None, "stop"),
            ("SessionEnd", None, "end"),
            ("Notification", "permission_prompt|idle_prompt|elicitation_dialog", "stop"),
        ),
    },
    "codex": {
        "label": "Codex",
        "home": ".codex",
        "file": ".codex/hooks.json",
        "processes": ("codex",),
        "events": (
            ("UserPromptSubmit", None, "start"),
            ("PreToolUse", None, "busy"),
            ("PostToolUse", None, "refresh"),
            ("SubagentStart", None, "substart"),
            ("SubagentStop", None, "substop"),
            ("PermissionRequest", None, "stop"),
            ("Stop", None, "stop"),
            ("Interrupt", None, "end"),
            ("SessionEnd", None, "end"),
        ),
    },
}


def hook_command(action, agent):
    return f'/usr/bin/python3 "$HOME/.dotfiles/scripts/agent-awake.py" --hook {action} {agent}'


def is_ours(entry):
    handlers = entry.get("hooks") if isinstance(entry.get("hooks"), list) else [entry]
    return any(isinstance(handler, dict) and HOOK_MARK.search(str(handler.get("command") or handler.get("bash") or ""))
               for handler in handlers)


# ── Hooks ─────────────────────────────────────────────────────────────────────


def agent_process(names):
    """The nearest ancestor process with one of these names, or None."""
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True,
                                 timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parents, commands = {}, {}
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
            commands[int(parts[0])] = os.path.basename(parts[2].strip())
    pid = os.getppid()
    for _ in range(20):
        if commands.get(pid) in names:
            return pid
        if pid not in parents or pid <= 1:
            return None
        pid = parents[pid]
    return None


def hold_name(agent, payload):
    session = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
    return NAME_CHARACTERS.sub("_", f"{agent}-{session}")[:120]


def subagent_id(payload):
    return NAME_CHARACTERS.sub("_", str(payload.get("agent_id") or payload.get("agentId") or ""))[:60]


def release(path):
    """Release a hold unless it already is, so a second release doesn't restart its grace period."""
    if path.exists() and "released" not in path.read_text():
        write_hold(path, "released\n")


def end_hold(path):
    """Mark a session ended: late tool hooks no longer hold it, only a new prompt does. An ended hold the
    helper did not already count as working gets no grace period."""
    content = path.read_text() if path.exists() else "released\n"
    if "ended" in content:
        return
    previous = path.stat().st_mtime if path.exists() else time.time() - RELEASE_GRACE_SECONDS
    write_hold(path, "released\nended\n")
    if "released" in content:
        os.utime(path, (previous, previous))


def end_own_hold(directory, agent, sub_id):
    """End the session a Copilot subagent's own tool calls run in, under its ID, also before it has one."""
    if sub_id and agent == "copilot":
        end_hold(directory / hold_name(agent, {"session_id": sub_id}))


def subagents_of_own_session(directory, path):
    """Active holds that parent sessions keep for the subagent whose own session the hold path is."""
    session = path.name.split("-", 1)[-1]
    return [sub for sub in sorted(directory.glob(f"*.sub.{session}"))
            if sub.name.split("-", 1)[0] == path.name.split("-", 1)[0] and "released" not in sub.read_text()]


def subagent_holds(path):
    """The session's active subagent holds, oldest first."""
    holds = []
    for sub in sorted(path.parent.glob(f"{path.name}.sub.*")):
        try:
            if "released" not in sub.read_text():
                holds.append(sub)
        except OSError:
            pass
    return holds


def write_hold(path, content):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".", delete=False) as temporary:
        temporary.write(content)
    # A rename changes the folder, which is what the helper's launchd WatchPaths notices.
    os.replace(temporary.name, path)


def clean_holds(directory, now):
    for path in directory.iterdir():
        try:
            if now - path.lstat().st_mtime > STALE_HOLD_SECONDS:
                path.unlink()
        except OSError:
            pass


def observe_session(action, agent, payload):
    """Publish the session selected by a wrapped terminal, independently of its awake hold."""
    if action != "start" or agent not in ("copilot", "claude") \
            or os.environ.get("DOTFILES_AGENT_KIND") != agent:
        return
    marker = os.environ.get("DOTFILES_AGENT_SESSION", "")
    session = payload.get("session_id") or payload.get("sessionId") or ""
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    if not re.fullmatch(uuid, marker) or not isinstance(session, str) or not re.fullmatch(uuid, session):
        return
    try:
        directory = Path.home() / ".cache/agent-sessions"
        directory.mkdir(parents=True, exist_ok=True)
        write_hold(directory / marker, f"{session}\n")
    except OSError as error:
        print(f"Error: could not save the terminal's session observation: {error}", file=sys.stderr)


def run_hook(action, agent, stdin=None):
    """Start, mark busy, refresh or release this session's hold, or add or release a subagent hold. Never
    prints, and never fails the agent's hook. A busy hold, with a tool call running, lasts BUSY_SECONDS
    instead of LEASE_SECONDS."""
    try:
        try:
            payload = json.loads((stdin or sys.stdin).read() or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        observe_session(action, agent, payload)
        directory = agent_awake.holds_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / hold_name(agent, payload)
        content = path.read_text() if path.exists() else "released"
        held = "released" not in content
        if action == "error":
            # A turn that fails, such as when the model can't be reached after its retries, gets no agentStop.
            if payload.get("recoverable") not in (False, "false"):
                return 0
            action = "stop"
        sub_id = subagent_id(payload)
        if action == "substart":
            pid = agent_process(AGENTS[agent]["processes"]) or ""
            write_hold(directory / f"{path.name}.sub.{sub_id or f'{time.time_ns()}-{os.getpid()}'}", f"{pid}\n")
        elif action == "substop":
            # A stop with an ID ends only that subagent; one without ends the oldest.
            named = directory / f"{path.name}.sub.{sub_id}"
            for sub in [named] if sub_id else subagent_holds(path)[:1]:
                release(sub)
            end_own_hold(directory, agent, sub_id)
        elif action in ("stop", "end"):
            # A Copilot subagent that stops, or fails, under its own ID also ends its hold in the parent.
            for hold in [path] + subagents_of_own_session(directory, path):
                release(hold)
            if action == "end":
                for sub in subagent_holds(path):
                    release(sub)
                    end_own_hold(directory, agent, sub.name.split(".sub.", 1)[1])
                end_hold(path)
        elif "ended" in content and action != "start":
            return 0
        elif held and action != "start" and (action == "busy") == ("busy" in content):
            os.utime(path)
        else:
            # A prompt, or a tool call after a release, looks up the agent's process, which may be new
            # after a resumed session; a hold that only changes between busy and idle keeps it.
            if held and action != "start":
                pid = content.split("\n", 1)[0]
            else:
                pid = agent_process(AGENTS[agent]["processes"]) or ""
            write_hold(path, f"{pid}\nbusy\n" if action == "busy" else f"{pid}\n")
        if action in ("busy", "refresh"):
            # Tool calls from the session keep its subagent holds current, except those of Copilot subagents
            # with tool calls of their own: those, whose session is the subagent's ID, keep its hold current,
            # so a subagent that dies without a subagentStop doesn't stay held by its parent's activity.
            parent_held = [sub for sub in subagent_holds(path)
                           if not (directory / f"{agent}-{sub.name.split('.sub.', 1)[1]}").exists()]
            for sub in parent_held + subagents_of_own_session(directory, path):
                os.utime(sub)
        clean_holds(directory, time.time())
    except Exception:  # noqa: BLE001 - a hook must never get in the agent's way
        pass
    return 0


# ── Install and uninstall ─────────────────────────────────────────────────────


def read_json(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def write_json(path, data):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
        json.dump(data, temporary, indent=2)
        temporary.write("\n")
    os.chmod(temporary.name, mode)
    os.replace(temporary.name, path)


def without_ours(hooks):
    """The hooks mapping without this script's handlers, keeping other handlers that share a matcher
    group, and without the groups and events that leaves empty."""
    kept = {}
    for event, entries in hooks.items():
        if isinstance(entries, list):
            remaining = []
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                    handlers = [handler for handler in entry["hooks"]
                                if not (isinstance(handler, dict) and is_ours(handler))]
                    if handlers:
                        remaining.append(entry if len(handlers) == len(entry["hooks"])
                                         else {**entry, "hooks": handlers})
                elif not (isinstance(entry, dict) and is_ours(entry)):
                    remaining.append(entry)
            if not remaining:
                continue
            entries = remaining
        kept[event] = entries
    return kept


def our_entries(agent):
    entries = {}
    for event, matcher, action in AGENTS[agent]["events"]:
        if agent == "copilot":
            entry = {"type": "command", "bash": hook_command(action, agent), "timeoutSec": HOOK_TIMEOUT_SECONDS}
        else:
            entry = {"hooks": [{"type": "command", "command": hook_command(action, agent),
                                "timeout": HOOK_TIMEOUT_SECONDS}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        entries.setdefault(event, []).append(entry)
    return entries


def hook_file(agent):
    return Path.home() / AGENTS[agent]["file"]


def agent_present(agent):
    return (Path.home() / AGENTS[agent]["home"]).is_dir()


def add_hooks(agent):
    path = hook_file(agent)
    if agent == "copilot":
        write_json(path, {"version": 1, "hooks": our_entries(agent)})
        return
    data = read_json(path)
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    hooks = without_ours(hooks)
    for event, entries in our_entries(agent).items():
        hooks[event] = [*hooks.get(event, []), *entries]
    data["hooks"] = hooks
    write_json(path, data)


def remove_hooks(agent):
    """Remove this script's hooks; returns whether there were any."""
    path = hook_file(agent)
    if not path.exists():
        return False
    if agent == "copilot":
        path.unlink()
        return True
    data = read_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    kept = without_ours(hooks)
    if kept == hooks:
        return False
    if kept:
        data["hooks"] = kept
    else:
        del data["hooks"]
    if agent == "codex" and not data:
        path.unlink()
    else:
        write_json(path, data)
    return True


def hooks_state(agent):
    """"absent" without the agent, else "installed", "missing", "partial" or an error message."""
    if not agent_present(agent):
        return "absent"
    try:
        data = read_json(hook_file(agent))
    except (OSError, ValueError) as error:
        return f"unreadable: {error}"
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    found = {event for event, entries in hooks.items()
             if isinstance(entries, list) and any(isinstance(entry, dict) and is_ours(entry) for entry in entries)}
    wanted = {event for event, _, _ in AGENTS[agent]["events"]}
    if not found:
        return "missing"
    return "installed" if found >= wanted else "partial"


def sudo_helper(*arguments):
    command = ["sudo", "/bin/bash", str(DAEMON_SCRIPT), *arguments]
    if subprocess.run(command).returncode != 0:
        raise RuntimeError("the root helper could not be changed; nothing else was changed")


def install(battery):
    if sys.platform != "darwin":
        raise RuntimeError("agent-awake only works on macOS")
    agent_awake.holds_dir().mkdir(parents=True, exist_ok=True)
    agent_awake.wakes_dir().mkdir(parents=True, exist_ok=True)
    print("Installing the root helper; sudo may ask for your password.", flush=True)
    sudo_helper("--install", "--home", str(Path.home()), "--battery", str(battery))
    output.section("Installed")
    output.row("ok", "Root helper", f"{agent_awake.DAEMON_PLIST}, sleep allowed below {battery}% on battery")
    for agent, details in AGENTS.items():
        if not agent_present(agent):
            output.row("info", details["label"], "not installed, skipped")
            continue
        try:
            add_hooks(agent)
        except (OSError, ValueError) as error:
            output.row("fail", details["label"], f"hooks not added: {error}")
            continue
        output.row("ok", details["label"], f"hooks in ~/{details['file']}")
    if agent_present("codex"):
        output.note("Codex runs new hooks only after you trust them: open /hooks in the Codex CLI once.")
    output.note("Agents load hooks when a session starts, so restart running sessions.")
    return 0


def uninstall():
    print("Removing the root helper; sudo may ask for your password.", flush=True)
    sudo_helper("--uninstall")
    output.section("Removed")
    output.row("ok", "Root helper", "unloaded and removed; sleep allowed")
    for agent, details in AGENTS.items():
        try:
            removed = remove_hooks(agent)
        except (OSError, ValueError) as error:
            output.row("fail", details["label"], f"hooks not removed: {error}")
            continue
        if removed:
            output.row("ok", details["label"], f"hooks removed from ~/{details['file']}")
    state = agent_awake.state_dir()
    for directory in (agent_awake.holds_dir(), agent_awake.wakes_dir()):
        if directory.is_dir():
            for path in directory.iterdir():
                path.unlink(missing_ok=True)
            directory.rmdir()
    if state.is_dir() and not any(state.iterdir()):
        state.rmdir()
    return 0


# ── Status ────────────────────────────────────────────────────────────────────


def command_output(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def describe_age(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def shorten(text, width):
    return text if len(text) <= width else text[:width - 1] + "\u2026"


def subagent_name(agent, parent_session, sub):
    """A Copilot subagent's display name from its parent session's events, or None."""
    if agent != "copilot":
        return None
    events = Path.home() / ".copilot/session-state" / parent_session / "events.jsonl"
    for entry in json_lines_from_end(events, '"subagent.started"', sub):
        if entry.get("agentId") == sub:
            return entry.get("data", {}).get("agentDisplayName")
    return None


def describe_hold(path, now):
    """(kind, detail) for a hold file, as the helper would count it."""
    age = now - path.stat().st_mtime
    content = path.read_text(errors="replace")
    if "released" in content:
        if age < RELEASE_GRACE_SECONDS:
            return "warn", (f"finished {describe_age(age)} ago; keeps the Mac awake "
                            f"{describe_age(RELEASE_GRACE_SECONDS - age)} more in case it continues")
        return "info", f"finished {describe_age(age)} ago"
    pid = content.split("\n", 1)[0].strip()
    if pid.isdigit() and not process_alive(int(pid)):
        return "info", f"agent process {pid} has exited"
    busy = "busy" in content
    if age >= (BUSY_SECONDS if busy else LEASE_SECONDS):
        return "info", f"no activity for {describe_age(age)}, expired"
    if busy:
        return "ok", f"running a tool call, started {describe_age(age)} ago"
    return "ok", f"working, last activity {describe_age(age)} ago"


def status():
    output.section("Root helper")
    battery = None
    if not agent_awake.installed():
        output.row("info", "LaunchDaemon", "not installed; run agent-awake --install")
    else:
        try:
            with agent_awake.DAEMON_PLIST.open("rb") as plist_file:
                arguments = plistlib.load(plist_file).get("ProgramArguments", [])
            battery = arguments[arguments.index("--battery") + 1]
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
        output.row("ok", "LaunchDaemon", f"installed, sleep allowed below {battery or '?'}% on battery")
        try:
            current = agent_awake.HELPER.read_bytes() == DAEMON_SCRIPT.read_bytes()
        except OSError:
            current = False
        if current:
            output.row("ok", "Helper", "up to date")
        else:
            output.row("warn", "Helper", "outdated; run agent-awake --install to update it")

    output.section("Power")
    settings = command_output("pmset", "-g")
    disabled = any(line.split()[:2] == ["SleepDisabled", "1"] for line in settings.splitlines())
    output.row("warn" if disabled else "info", "Sleep", "disabled (kept awake)" if disabled else "allowed")
    power = command_output("pmset", "-g", "batt")
    match = re.search(r"(\d+)%", power)
    source = "battery" if "'Battery Power'" in power else "power adapter"
    output.row("info", "Power source", f"{source}{f', {match.group(1)}%' if match else ''}")

    output.section("Hooks")
    for agent, details in AGENTS.items():
        state = hooks_state(agent)
        if state == "absent":
            output.row("info", details["label"], "not installed")
        elif state == "installed":
            output.row("ok", details["label"], f"~/{details['file']}")
        elif state in ("missing", "partial"):
            output.row("warn", details["label"], f"hooks {state}; run agent-awake --install")
        else:
            output.row("fail", details["label"], state)

    output.section("Holds")
    now = time.time()
    holds = sorted(agent_awake.holds_dir().glob("[!.]*")) if agent_awake.holds_dir().is_dir() else []
    wakes = sorted(agent_awake.wakes_dir().glob("[!.]*")) if agent_awake.wakes_dir().is_dir() else []
    # A subagent's hold in its parent session and the hold its own tool calls create (Copilot) are one row.
    rows, parents = {}, {}
    for path in holds:
        try:
            kind, detail = describe_hold(path, now)
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = path.name
        if ".sub." in path.name:
            parent, sub = path.name.split(".sub.", 1)
            if not UNNAMED_SUBAGENT.match(sub):
                key = f"{parent.split('-', 1)[0]}-{sub}"
            parents[key] = parent
        if key not in rows or (KIND_ORDER[kind], -mtime) < (KIND_ORDER[rows[key][0]], -rows[key][2]):
            rows[key] = (kind, detail, mtime)
    lines = []
    for key, (kind, detail, mtime) in rows.items():
        agent, session = key.split("-", 1) if "-" in key else (key, "")
        who = agent.capitalize()
        name = session_name(agent, session)
        if key in parents:
            parent_session = parents[key].split("-", 1)[-1]
            parent_name = session_name(agent, parent_session)
            parent_label = f"\u201c{shorten(parent_name, 30)}\u201d" if parent_name else parent_session[:8]
            who = f"{who} sub-agent of {parent_label}"
        fallback = key[:22]
        if ".sub." in key:
            name, fallback = None, parents[key][:17] + " sub"
        elif key in parents:
            name = name or subagent_name(agent, parent_session, session)
        lines.append((kind, shorten(name, 22) if name else fallback, f"{who}, {detail}", mtime))
    # Holds keeping the Mac awake first, then the rest; the most recent activity first in each.
    lines.sort(key=lambda row: (row[0] not in ("ok", "warn"), -row[3]))
    awake = sum(1 for row in lines if row[0] in ("ok", "warn"))
    for kind, label, detail, _ in lines[:awake + RECENT_HOLDS]:
        output.row(kind, label, detail)
    if len(lines) > awake + RECENT_HOLDS:
        output.note(f"{len(lines) - awake - RECENT_HOLDS} older finished or expired holds not shown")
    for path in wakes:
        stamp = path.name.rsplit(".", 1)[-1]
        when = datetime.fromtimestamp(int(stamp)).strftime("%d.%m.%Y kl. %H.%M") if stamp.isdigit() else "?"
        output.row("info", path.name.rsplit(".", 1)[0][:22], f"wake requested for {when}")
    if not holds and not wakes:
        print("  None.")

    if agent_awake.LOG.exists():
        output.section("Recent helper log")
        try:
            lines = agent_awake.LOG.read_text(errors="replace").splitlines()[-LOG_LINES:]
        except OSError as error:
            lines = [f"Could not read {agent_awake.LOG}: {error}"]
        for line in lines:
            print(f"  {line}")
    return 0


def argument_parser():
    parser = argparse.ArgumentParser(
        prog="agent-awake",
        allow_abbrev=False,
        description=command_help.description("agent-awake"),
        epilog=command_help.help_epilog(
            "agent-awake", "Open sessions that are not working do not keep the Mac awake. "
            "Installation is never done automatically."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--install", action="store_true",
                         help="install or update the root helper and the agent hooks on this Mac")
    actions.add_argument("--uninstall", action="store_true",
                         help="remove the root helper and the hooks, and allow sleep again")
    actions.add_argument("--status", action="store_true",
                         help="show the helper, hooks and current holds (the default)")
    actions.add_argument("--hook", nargs=2, metavar=("ACTION", "AGENT"), help=argparse.SUPPRESS)
    parser.add_argument("--battery", type=int, default=DEFAULT_BATTERY, metavar="PERCENT",
                        help=f"with --install, let the Mac sleep on battery below this charge "
                             f"(default {DEFAULT_BATTERY})")
    return parser


def main(arguments):
    parser = argument_parser()
    options = parser.parse_args(arguments)
    if options.hook:
        action, agent = options.hook
        if action not in ("start", "busy", "refresh", "stop", "end", "error", "substart", "substop") \
                or agent not in AGENTS:
            return 0
        return run_hook(action, agent)
    if not 1 <= options.battery <= 99:
        parser.error("--battery must be between 1 and 99")
    if options.install:
        return install(options.battery)
    if options.uninstall:
        return uninstall()
    return status()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"agent-awake: {error}", file=sys.stderr)
        sys.exit(1)
