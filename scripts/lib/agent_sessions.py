"""Shared agent session paths, titles and transcript readers."""

import functools
import json
import os
from pathlib import Path
import re
from typing import NamedTuple


UUID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"


class RestorationRecord(NamedTuple):
    cwd: str
    launcher: str
    pid: str
    started: str
    agent: str
    app: str
    session: str
    observed: frozenset
    invalid_observation: bool


def restoration_record(lines, observations):
    """Parse marker and observation metadata without resolving titles or transcripts."""
    if len(lines) not in (7, 8):
        return None
    cwd, launcher, _, pid, started, initial, agent = lines[:7]
    if not cwd or launcher not in ("copilot", "claude", "cplt") \
            or agent not in ("copilot", "claude") or not pid.isdigit():
        return None
    session = initial if re.fullmatch(UUID, initial) else ""
    observed = {session} if session else set()
    invalid = bool(initial and not session)
    valid = []
    for stamp, content in observations:
        if content and re.fullmatch(UUID, content[0]):
            valid.append((stamp, content[0]))
            observed.add(content[0])
            if len(content) != 1:
                invalid = True
        else:
            invalid = True
    if valid:
        valid.sort(key=lambda item: item[0], reverse=True)
        session = valid[0][1]
    return RestorationRecord(cwd, launcher, pid, started, agent,
                             lines[7] if len(lines) == 8 else "",
                             session, frozenset(observed), invalid)


def agent_home(agent):
    variable = {"copilot": "COPILOT_HOME", "claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}[agent]
    return Path(os.environ.get(variable) or Path.home() / f".{agent}")


def lines_from_end(path, limit=64 << 20):
    """Lines of a file, last first, reading at most limit bytes from its end."""
    try:
        with path.open("rb") as stream:
            position = stream.seek(0, os.SEEK_END)
            stop, carry = max(0, position - limit), b""
            while position > stop:
                size = min(1 << 20, position - stop)
                position -= size
                stream.seek(position)
                parts = (stream.read(size) + carry).split(b"\n")
                carry = parts.pop(0) if position > stop else b""
                for line in reversed(parts):
                    yield line.decode(errors="replace")
            if carry:
                yield carry.decode(errors="replace")
    except OSError:
        return


def json_lines_from_end(path, *markers):
    for line in lines_from_end(path):
        if all(marker in line for marker in markers):
            try:
                yield json.loads(line)
            except ValueError:
                continue


@functools.lru_cache(maxsize=None)
def session_name(agent, session):
    """The name an agent shows for a session, or None."""
    if not session or agent not in ("copilot", "claude", "codex"):
        return None
    home = agent_home(agent)
    if agent == "copilot":
        try:
            for line in (home / "session-state" / session / "workspace.yaml").read_text().splitlines():
                if line.startswith("name:"):
                    return line[5:].strip().strip("'\"") or None
        except OSError:
            return None
    if agent == "codex":
        for entry in json_lines_from_end(home / "session_index.jsonl", session):
            if entry.get("id") == session and entry.get("thread_name"):
                return entry["thread_name"]
    if agent == "claude":
        for transcript in (home / "projects").glob(f"*/{session}.jsonl"):
            titles = {}
            for entry in json_lines_from_end(transcript, "-title"):
                for kind, field in (("custom-title", "customTitle"), ("ai-title", "aiTitle")):
                    if entry.get("type") == kind and entry.get(field):
                        titles.setdefault(kind, entry[field])
                if "custom-title" in titles:
                    break
            return titles.get("custom-title") or titles.get("ai-title")
    return None
