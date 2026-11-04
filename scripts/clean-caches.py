#!/usr/bin/env python3
"""Preview global developer caches and explicitly select cleaners to run."""

from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.cache_cleaners import main as clean_caches  # noqa: E402


def main(argv=None):
    return clean_caches(argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        sys.exit(130)
