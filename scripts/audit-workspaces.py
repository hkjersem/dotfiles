#!/usr/bin/env python3
"""Render size-ranked registered worktrees using the audit's Git assessments."""

import argparse
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
from files import ScanProgress, display, human_size, output, scan_sizes  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Read NUL-delimited path, branch and assessment triples on stdin.")
    parser.parse_args()
    fields = sys.stdin.buffer.read().split(b"\0")
    if fields[-1:] == [b""]:
        fields.pop()
    if len(fields) % 3:
        print("Error: incomplete workspace report input.", file=sys.stderr)
        return 1
    rows = []
    warnings = {}
    progress = ScanProgress(Path("/"), True)
    try:
        for index in range(0, len(fields), 3):
            path, branch, assessment = (os.fsdecode(value) for value in fields[index:index + 3])
            folder = Path(path)
            size = None
            denied = {}
            try:
                info = folder.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    assessment = "Keep: not a real directory; symlinks are not followed"
                else:
                    totals = scan_sizes(folder, False, False, [".git"], include_root=True,
                                        warnings=denied, progress=progress)
                    size = None if denied else totals[Path(".")]
            except FileNotFoundError:
                assessment = "Review: registered directory is missing"
            except PermissionError as exc:
                denied[folder] = exc
            warnings.update(denied)
            rows.append((size, path, branch or "(detached)", assessment))
    except OSError as exc:
        print(f"Error: workspace size scan failed: {display(exc)}", file=sys.stderr)
        return 1
    finally:
        progress.close()
    output.section("Workspace disk usage")
    output.note("Git assessments use local refs from the last fetch.")
    for size, path, branch, assessment in sorted(rows, key=lambda row: (row[0] is None, -(row[0] or 0), row[1])):
        print(f"  {human_size(size) if size is not None else 'Unknown':>12}  "
              f"{output.listing_name(display(path), 'folder')}")
        output.note(f"{display(branch)} — {display(assessment)}")
    if warnings:
        output.row("warn", "Incomplete sizes", "Permission-denied entries were skipped.")
        for path, error in sorted(warnings.items()):
            output.row("warn", display(path), display(error.strerror or str(error)))
    return 2 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
