"""Shared paths and wake requests for agent-awake.

Agent hooks write hold files to ~/.cache/agent-awake/holds and other commands write wake requests to
~/.cache/agent-awake/wake; the root helper, scripts/agent-awake-daemon.sh, reads both. The folder is under
~/.cache because the cplt sandbox lets agents write there in every repository.
"""

from pathlib import Path

LABEL = "com.hkjersem.agent-awake"
DAEMON_PLIST = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
HELPER = Path("/Library/PrivilegedHelperTools") / f"{LABEL}.sh"
LOG = Path("/var/log/agent-awake.log")


def state_dir():
    return Path.home() / ".cache/agent-awake"


def holds_dir():
    return state_dir() / "holds"


def wakes_dir():
    return state_dir() / "wake"


def installed():
    return DAEMON_PLIST.exists()


def cancel_wakes(source):
    directory = wakes_dir()
    if directory.is_dir():
        for path in directory.glob(f"{source}.*"):
            path.unlink(missing_ok=True)


def request_wake(source, moment):
    """Ask the root helper to wake the Mac for moment, an aware datetime, replacing the source's earlier
    request. Returns the request file, or None when agent-awake is not installed."""
    if not installed():
        return None
    directory = wakes_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cancel_wakes(source)
    path = directory / f"{source}.{int(moment.timestamp())}"
    path.touch()
    return path
