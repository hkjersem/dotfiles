#!/usr/bin/env python3
"""Filter and size a NUL-delimited cleanup plan before confirmation."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import time

sys.dont_write_bytecode = True
from files import ScanProgress, age_arg, display, human_size, latest_mtime, output, scan_sizes  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Preview allocated sizes for a NUL-delimited cleanup plan on stdin.",
    )
    parser.add_argument("--sort", choices=("path", "size"), default="path",
                        help="sort preview paths alphabetically or by size, largest first")
    parser.add_argument("--older-than", type=age_arg, metavar="AGE")
    parser.add_argument("--targets-output", type=Path, help="write the filtered NUL-delimited plan")
    parser.add_argument("--check-age", action="store_true", help="refuse targets no longer old enough")
    disk = parser.add_mutually_exclusive_group()
    disk.add_argument("--available-bytes", action="store_true", help="print current filesystem free bytes")
    disk.add_argument("--space-after", type=int, metavar="BEFORE",
                      help="compare current filesystem free bytes with BEFORE")
    args = parser.parse_args(argv)
    progress = ScanProgress(Path.cwd(), True)
    total = 0
    try:
        if args.available_bytes or args.space_after is not None:
            available = shutil.disk_usage(Path.cwd()).free
            if args.available_bytes:
                print(available)
            else:
                change = available - args.space_after
                output.note(f"Available on current filesystem: {human_size(args.space_after)} → "
                            f"{human_size(available)}")
                output.note(f"Observed change: {'+' if change >= 0 else '-'}{human_size(abs(change))}")
                output.note("Other activity and APFS snapshots affect this; other filesystems are not measured.")
            return 0
        rows = []
        selected = []
        cutoff = time.time() - args.older_than if args.older_than is not None else None
        for raw in sys.stdin.buffer.read().split(b"\0"):
            if not raw:
                continue
            path = Path(os.path.abspath(os.fsdecode(raw)))
            info = path.lstat()
            if cutoff is not None and latest_mtime(path, progress=progress) > cutoff:
                if args.check_age:
                    raise OSError(f"target was modified recently; cleanup aborted: {display(path)}")
                continue
            if args.check_age:
                continue
            folder = stat.S_ISDIR(info.st_mode)
            if folder:
                warnings = {}
                size = scan_sizes(path, False, False, [], include_root=True,
                                  warnings=warnings, progress=progress)[Path(".")]
                if warnings:
                    for denied, error in warnings.items():
                        print(f"Error: cannot size {display(denied)}: {display(error)}", file=sys.stderr)
                    return 1
            elif stat.S_ISREG(info.st_mode):
                size = info.st_blocks * 512
            else:
                raise OSError(f"cleanup target changed type: {display(path)}")
            name = display(path.relative_to(Path.cwd()))
            rows.append((size, name, folder))
            selected.append(raw)
            total += size
        progress.close()
        if args.check_age:
            return 0
        if args.targets_output is not None:
            args.targets_output.write_bytes(b"".join(raw + b"\0" for raw in selected))
        if not rows:
            print("Nothing to clean.")
            return 0
        rows.sort(key=lambda row: (-row[0], row[1]) if args.sort == "size" else (row[1],))
        output.section("Will delete")
        for size, name, folder in rows:
            print(f"  {human_size(size):>12}  {output.listing_name(name, 'folder' if folder else 'file')}")
        output.row("info", "Estimated total", human_size(total))
        output.note("Allocated file bytes; hard links and shared APFS blocks may overstate reclaimed space.")
        return 0
    except OSError as exc:
        print(f"Error: cleanup preview or disk-space query failed: {display(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    finally:
        progress.close()


if __name__ == "__main__":
    sys.exit(main())
