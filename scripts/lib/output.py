"""Shared terminal output style for this repository's Python scripts; mirrors scripts/lib/output.sh.

scripts/tests/test_output.py checks that both print the same bytes. Colours apply only when the stream is a
terminal and NO_COLOR is unset, so pipes and logs stay plain; the symbols carry the meaning without colour.
"""

import os
import sys

CODES = {
    "bold": "\033[1m", "dim": "\033[2m", "red": "\033[0;31m", "green": "\033[0;32m",
    "yellow": "\033[1;33m", "cyan": "\033[0;36m", "reset": "\033[0m",
}
MARKS = {"ok": ("green", "✓"), "warn": ("yellow", "!"), "fail": ("red", "✗"), "info": ("dim", "•")}
RULE = "──────────────────────────────────"


def colored(stream=None):
    stream = stream or sys.stdout
    return stream.isatty() and not os.environ.get("NO_COLOR")


def paint(text, color, stream=None):
    return f"{CODES[color]}{text}{CODES['reset']}" if colored(stream) else text


def mark(kind, stream=None):
    color, symbol = MARKS[kind]
    return paint(symbol, color, stream)


def section(title, stream=None):
    stream = stream or sys.stdout
    print(f"\n{paint(f'── {title} {RULE}', 'bold', stream)}", file=stream)


def subsection(title, stream=None):
    stream = stream or sys.stdout
    print(f"\n{paint(f'  ▸ {title}', 'bold', stream)}", file=stream)
