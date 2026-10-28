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
WIDTH = 60


def colored(stream=None):
    stream = stream or sys.stdout
    return stream.isatty() and not os.environ.get("NO_COLOR")


def paint(text, color, stream=None):
    return f"{CODES[color]}{text}{CODES['reset']}" if colored(stream) else text


def listing_name(text, kind, stream=None):
    """Use BSD ls's LSCOLORS for folder and symlink names; leave files plain."""
    if not colored(stream) or kind not in ("folder", "symlink"):
        return text
    palette = os.environ.get("LSCOLORS") or "Exfxcxdxbxegedabagacad"
    offset = 0 if kind == "folder" else 2
    pair = palette[offset:offset + 2]
    if len(pair) != 2 or any(char not in "abcdefghABCDEFGHx" for char in pair):
        return text
    codes = ["1"] if any(char.isupper() for char in pair) else []
    for char, base in zip(pair, (30, 40)):
        if char != "x":
            codes.append(str(base + ord(char.lower()) - ord("a")))
    return f"\033[{';'.join(codes)}m{text}{CODES['reset']}" if codes else text


def mark(kind, stream=None):
    color, symbol = MARKS[kind]
    return paint(symbol, color, stream)


def ruled(text, char):
    """TEXT followed by a rule of CHAR up to WIDTH characters, at least three."""
    return text + char * max(3, WIDTH - len(text))


def section(title, stream=None):
    stream = stream or sys.stdout
    print(f"\n{paint(ruled(f'─ {title} ', '─'), 'bold', stream)}", file=stream)


def subsection(title, stream=None):
    stream = stream or sys.stdout
    print(f"\n{paint(f'  ▸ {title}', 'bold', stream)}", file=stream)


def title(name, stream=None):
    stream = stream or sys.stdout
    print(paint(ruled(f"━ {name} ", "━"), "bold", stream), file=stream)


def context(*parts, stream=None):
    stream = stream or sys.stdout
    print(paint(' │ '.join(parts), 'dim', stream), file=stream)


def dry_run(hint="", stream=None):
    stream = stream or sys.stdout
    print(f"{paint('Dry run', 'yellow', stream)} — nothing will be changed.{' ' + hint if hint else ''}", file=stream)


def progress(text, stream=None):
    stream = stream or sys.stderr
    if stream.isatty():
        print(f"\r\033[K{paint(text, 'dim')}", end="", file=stream, flush=True)


def progress_done(stream=None):
    stream = stream or sys.stderr
    if stream.isatty():
        print("\r\033[K", end="", file=stream, flush=True)


def note(text, stream=None):
    print(f"    {text}", file=stream or sys.stdout)


def row(kind, label, detail, stream=None):
    stream = stream or sys.stdout
    print(f"  {mark(kind, stream)} {label:<22} {detail}", file=stream)


def table_header(format, *columns, stream=None):
    stream = stream or sys.stdout
    print(f"  {paint(format % columns, 'bold', stream)}", file=stream)


def summary(issues, warnings, all_good, stream=None):
    stream = stream or sys.stdout
    section("Summary", stream)
    if not issues and not warnings:
        print(f"  {mark('ok', stream)} {all_good}", file=stream)
    if issues:
        print(f"  {mark('fail', stream)} {issues} issue(s) found", file=stream)
    if warnings:
        print(f"  {mark('warn', stream)} {warnings} warning(s) found", file=stream)
