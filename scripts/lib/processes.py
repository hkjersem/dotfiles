"""Read process metadata without collecting command-line arguments."""

from dataclasses import dataclass
import os
import subprocess


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    started: str
    command: str


def process_snapshot():
    """Return one strict snapshot; callers decide whether failure is fatal."""
    result = subprocess.run(
        ["/bin/ps", "-A", "-o", "pid=,ppid=,lstart=,comm="],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or result.stderr.strip():
        raise OSError("Could not inspect processes: " + (result.stderr.strip() or f"ps exited {result.returncode}"))
    processes = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 7)
        if (len(parts) != 8 or not parts[0].isdigit() or not parts[1].isdigit()
                or not parts[4].isdigit() or not parts[6].isdigit()):
            raise OSError("Could not inspect processes: unrecognised ps output")
        pid = int(parts[0])
        if pid <= 0 or pid in processes:
            raise OSError("Could not inspect processes: invalid or duplicate PID")
        processes[pid] = Process(pid, int(parts[1]), " ".join(parts[2:7]), parts[7].strip())
    if not processes:
        raise OSError("Could not inspect processes: empty ps output")
    return processes
