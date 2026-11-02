#!/usr/bin/env python3
"""Rewrite commit dates and descendant parent hashes without touching the index."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import output


DATE = re.compile(rb"^(author|committer) (.*) ([0-9]+) ([+-][0-9]{4})$")
MONTHS = ("januar", "februar", "mars", "april", "mai", "juni",
          "juli", "august", "september", "oktober", "november", "desember")


def git(*args, data=None, env=None):
    result = subprocess.run(["git", *args], input=data, capture_output=True, env=env)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return result.stdout


def fields(raw):
    headers, separator, message = raw.partition(b"\n\n")
    if not separator:
        raise ValueError("commit has no header separator")
    lines = headers.split(b"\n")
    dates = {}
    for line in lines:
        match = DATE.fullmatch(line)
        if match:
            dates[match[1]] = (int(match[3]), match[4])
    if set(dates) != {b"author", b"committer"}:
        raise ValueError("commit has unsupported date fields")
    return lines, message, dates


def formatted(stamp, offset):
    text = offset.decode()
    minutes = (int(text[1:3]) * 60 + int(text[3:])) * (-1 if text[0] == "-" else 1)
    value = datetime.fromtimestamp(stamp, timezone(timedelta(minutes=minutes)))
    return f"{value.day}. {MONTHS[value.month - 1]} {value.year}, kl. {value:%H.%M.%S} {text}"


def explicit_date(value):
    env = dict(os.environ, GIT_AUTHOR_NAME="Date parser", GIT_AUTHOR_EMAIL="date@example.invalid",
               GIT_AUTHOR_DATE=value)
    ident = git("var", "GIT_AUTHOR_IDENT", env=env).rstrip()
    match = re.search(rb" ([0-9]+) ([+-][0-9]{4})$", ident)
    if not match:
        raise ValueError("Git did not return a valid date")
    return int(match[1]), match[2]


def amend(mode, value, commit="HEAD"):
    reference = git("rev-parse", "--symbolic-full-name", "HEAD").strip().decode()
    old = git("rev-parse", "--verify", "HEAD").strip()
    selected = git("rev-parse", "--verify", "--end-of-options", commit + "^{commit}").strip()
    if selected != old and not reference.startswith("refs/heads/"):
        raise ValueError("older commits require a checked-out branch; nothing was changed")
    plan = []
    current = old
    while True:
        lines, message, dates = fields(git("cat-file", "commit", current.decode()))
        parents = [line.split(b" ", 1)[1] for line in lines if line.startswith(b"parent ")]
        if selected != old and len(parents) > 1:
            raise ValueError("older-commit rewrites cannot include merges; nothing was changed")
        if any(line.startswith((b"gpgsig ", b"gpgsig-sha256 ")) for line in lines):
            raise ValueError("signed commits are not supported; nothing was changed")
        plan.append((current, lines, message))
        if current == selected:
            break
        if not parents:
            raise ValueError("commit is not an ancestor of HEAD; nothing was changed")
        current = parents[0]
    if mode == "--date":
        target = explicit_date(value)
        targets = {kind: target for kind in dates}
    elif mode == "--ago-seconds":
        stamp = int(time.time()) - int(value)
        offset = datetime.fromtimestamp(stamp).astimezone().strftime("%z").encode()
        targets = {kind: (stamp, offset) for kind in dates}
    else:
        targets = {kind: (stamp - int(value), offset) for kind, (stamp, offset) in dates.items()}
    if any(stamp < 0 for stamp, _ in targets.values()):
        raise ValueError("dates before the Unix epoch are not supported")
    subject = message.split(b"\n", 1)[0].decode(errors="replace")
    output.section(f"{selected[:12].decode()} {subject}")
    for label, values in (("Before", dates), ("After", targets)):
        output.table_header("%s", label)
        if values[b"author"] == values[b"committer"]:
            output.note("Both       " + formatted(*values[b"author"]))
        else:
            for kind in (b"author", b"committer"):
                output.note(f"{kind.decode().capitalize():<10} " + formatted(*values[kind]))
    if len(plan) > 1:
        output.note(f"Rewrites {len(plan)} hashes on {reference.removeprefix('refs/heads/')}; "
                    "other branches stay unchanged.")
    for parent in parents:
        _, _, parent_dates = fields(git("cat-file", "commit", parent.decode()))
        for kind in (b"author", b"committer"):
            if targets[kind][0] < parent_dates[kind][0]:
                output.row("warn", kind.decode().capitalize(),
                           f"Earlier than parent {parent[:12].decode()}")
                output.note("Parent: " + formatted(*parent_dates[kind]))
    published = git("for-each-ref", "--format=%(refname:short)",
                    "--contains=" + selected.decode(), "refs/remotes").decode().splitlines()
    if published:
        output.row("warn", "Already pushed", ", ".join(published))
        output.note("Based on local remote-tracking refs; coordinate before force-pushing.")
    elif git("remote").strip() and not git("for-each-ref", "--format=%(refname)", "refs/remotes").strip():
        output.row("warn", "Pushed status unknown", "No local remote-tracking refs; no fetch was run.")
    sys.stdout.flush()
    library = Path(__file__).resolve().parent / "package-manager/_update-lib.sh"
    confirmation = subprocess.run([
        "/bin/bash", "-c",
        'source "$1" || exit 2; confirm_apply "Change these commit dates?" false false',
        "amend-date-confirm", str(library),
    ])
    if confirmation.returncode == 1:
        return 0
    if confirmation.returncode:
        raise ValueError("confirmation failed")
    if git("rev-parse", "--symbolic-full-name", "HEAD").strip().decode() != reference:
        raise ValueError("the checked-out branch changed during confirmation; nothing was changed")
    rewritten = {}
    for oid, lines, message in reversed(plan):
        replacement = []
        for line in lines:
            match = DATE.fullmatch(line)
            if oid == selected and match:
                stamp, offset = targets[match[1]]
                line = match[1] + b" " + match[2] + b" " + str(stamp).encode() + b" " + offset
            elif line.startswith(b"parent "):
                parent = line.split(b" ", 1)[1]
                line = b"parent " + rewritten.get(parent, parent)
            replacement.append(line)
        updated = b"\n".join(replacement) + b"\n\n" + message
        rewritten[oid] = git("hash-object", "-t", "commit", "-w", "--stdin", data=updated).strip()
    new = rewritten[old]
    git("update-ref", "-m", "amend-date: change commit dates", reference, new.decode(), old.decode())
    output.row("ok", "Updated", f"{selected[:12].decode()} -> {rewritten[selected][:12].decode()}")
    return 0


def main():
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print("Usage: git-amend-date.py <--ago-seconds|--shift-seconds|--date> <value> [commit]\n\n"
              "Internal helper; use git amend-date instead.\n\n  -h, --help  Show help")
        return 0
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ("--ago-seconds", "--shift-seconds", "--date"):
        print("Error: internal helper requires a mode and value.", file=sys.stderr)
        return 2
    try:
        return amend(*sys.argv[1:])
    except (OSError, ValueError, OverflowError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
