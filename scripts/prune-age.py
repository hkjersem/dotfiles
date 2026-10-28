#!/usr/bin/env python3
"""Check the optional pruning retention window using shared filesystem metadata."""

import argparse
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
from files import ScanProgress, age_arg, display, latest_mtime  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than", required=True, type=age_arg)
    parser.add_argument("--commit-time", required=True, type=int)
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    progress = ScanProgress(args.path or Path.cwd(), True)
    try:
        newest = args.commit_time
        if args.path:
            newest = max(newest, latest_mtime(args.path, excludes=(".git",), progress=progress))
        if newest > time.time() - args.older_than:
            print("retained by --older-than (recent commit or worktree modification)")
            return 1
        return 0
    except OSError as exc:
        print(f"age could not be verified: {display(exc)}")
        return 2
    finally:
        progress.close()


if __name__ == "__main__":
    sys.exit(main())
