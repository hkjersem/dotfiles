#!/usr/bin/env python3
"""Filter native zsh history snapshots without executing their contents."""

import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import output


def records(data):
    entries = []
    pending = b""
    for line in re.findall(rb"[^\n]*\n|[^\n]+$", data):
        pending += line
        if not line.endswith(b"\\\n"):
            entries.append(pending)
            pending = b""
    if pending:
        raise ValueError("history has an incomplete multiline entry")
    return entries


def command(entry):
    text = re.sub(rb"^: \d+:\d+;", b"", entry, count=1)
    if text.endswith(b"\n"):
        text = text[:-1]
    if text.endswith(b"\\ "):
        text = text[:-1]
    return text.replace(b"\\\n", b"\n")


def filtered(data, target, required):
    entries = records(data)
    matches = [index for index, entry in enumerate(entries) if command(entry) == target]
    if len(matches) > 1:
        raise ValueError("multiple saved copies match this event; nothing was deleted")
    if not matches and required:
        raise ValueError("the event could not be matched to the history snapshot; nothing was deleted")
    return b"".join(entry for index, entry in enumerate(entries) if index not in matches)


def delete(saved, snapshot, target):
    replacement = filtered(snapshot.read_bytes(), target.read_bytes(), required=True)
    lock = Path(str(saved) + ".LOCK")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("history is locked by another shell; try again") from None
    temporary = None
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"{os.getpid()} {socket.gethostname()}\n")
        try:
            metadata = saved.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("saved history must be a regular file, not a symlink")
        if metadata is not None:
            original = saved.read_bytes()
            updated = filtered(original, target.read_bytes(), required=False)
            if updated != original:
                descriptor, name = tempfile.mkstemp(prefix=".history-delete-", dir=saved.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
                snapshot.write_bytes(replacement)
                os.replace(temporary, saved)
                return
        snapshot.write_bytes(replacement)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink()


def main():
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print("Usage: history-delete.py <saved-history> <snapshot> <target-file>\n\n"
              "Internal helper; use history-delete in the current zsh instead.\n\n"
              "  -h, --help  Show help")
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "--confirm" and sys.argv[2].isdigit():
        try:
            text = Path(sys.argv[3]).read_text()
            for index, line in enumerate(text.splitlines()):
                safe = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in line)
                if index == 0:
                    output.row("info", f"Event {sys.argv[2]}", safe)
                else:
                    output.note(safe)
            sys.stdout.flush()
            library = Path(__file__).resolve().parent / "package-manager/_update-lib.sh"
            result = subprocess.run([
                "/bin/bash", "-c",
                'source "$1" || exit 2; confirm_apply "Delete this history entry?" false false',
                "history-delete-confirm", str(library),
            ])
            return result.returncode
        except OSError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    if len(sys.argv) == 3 and sys.argv[1] == "--report" and sys.argv[2].isdigit():
        output.row("ok", f"Event {sys.argv[2]}", "Deleted")
        return 0
    if len(sys.argv) != 4:
        print("Error: internal helper expects saved history, snapshot and target paths.", file=sys.stderr)
        return 2
    try:
        delete(*(Path(value) for value in sys.argv[1:]))
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
