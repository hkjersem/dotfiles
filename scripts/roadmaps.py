#!/usr/bin/env python3
"""Show what needs attention across roadmaps kept with the roadmap skill.

Each PATH is a repository or a folder whose direct subfolders are repositories.
Without PATHs, they are read one per line from ~/.config/dotfiles/roadmap-paths
(`#` starts a comment, `~` is expanded). Only roadmaps with the skill's
generated-list markers in ROADMAP.md are included. Read-only; exits 1 when a
roadmap has errors or cannot be read, or no paths are configured.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import os
import sys
from pathlib import Path
from typing import List, Optional

SKILL_SCRIPT = Path(__file__).resolve().parents[1] / "agents/skills/roadmap/scripts/roadmap.py"
_spec = importlib.util.spec_from_file_location("roadmap_skill", SKILL_SCRIPT)
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

INDEX = "ROADMAP.md"


def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "dotfiles", "roadmap-paths")


def home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def conforming(root: str, problems: List[str]) -> bool:
    index = os.path.join(root, INDEX)
    if not os.path.isfile(index):
        return False
    try:
        with open(index, encoding="utf-8") as handle:
            return rm.START in handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{home_short(index)}: cannot read ({exc.__class__.__name__})")
        return False


def find_roadmaps(folder: str, problems: List[str]) -> List[str]:
    """`folder` itself when it holds a roadmap, else its direct non-hidden
    subdirectories that do."""
    if conforming(folder, problems):
        return [folder]
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        problems.append(f"{home_short(folder)}: cannot list ({exc.__class__.__name__})")
        return []
    return [os.path.join(folder, n) for n in names
            if not n.startswith(".") and os.path.isdir(os.path.join(folder, n))
            and conforming(os.path.join(folder, n), problems)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="roadmaps", description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="a repository, or a folder whose direct subfolders are repositories")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="treat this date as today")
    args = parser.parse_args(argv)
    today = rm.parse_date(args.today) if args.today else datetime.date.today()
    if today is None:
        print("--today must be a YYYY-MM-DD date.")
        return 1

    entries = list(args.paths)
    if not entries:
        config = config_path()
        if not os.path.isfile(config):
            print(f"No paths given and {home_short(config)} does not exist. List repositories, "
                  "or folders that contain them, there one per line (`#` starts a comment).")
            return 1
        with open(config, encoding="utf-8") as handle:
            entries = [line.strip() for line in handle
                       if line.strip() and not line.strip().startswith("#")]

    roots: List[str] = []
    seen = set()
    notes: List[str] = []
    problems: List[str] = []
    for entry in entries:
        folder = os.path.abspath(os.path.expanduser(entry))
        if not os.path.isdir(folder):
            notes.append(f"{entry}: not a directory")
            continue
        for root in find_roadmaps(folder, problems):
            if os.path.realpath(root) not in seen:
                seen.add(os.path.realpath(root))
                roots.append(root)

    print(f"Roadmaps, {today.isoformat()}")
    quiet: List[str] = []
    failed = False
    for root in roots:
        repo = argparse.Namespace(root=root, index=INDEX, dir="roadmap", strict=False)
        try:
            report, briefs = rm.check_report(repo)
        except (OSError, UnicodeDecodeError) as exc:
            failed = True
            print(f"\n{os.path.basename(root)}  {home_short(root)}\n  cannot read the roadmap: {exc}")
            continue
        view = rm.Overview(briefs, today)
        verifying = [b for b in view.now if b.status == "verifying"]
        active = [b for b in view.now if b.status != "verifying"]
        name = os.path.basename(root)
        if not (view.due or verifying or active or report.errors):
            quiet.append(name + (f" ({len(view.can_start)} ready to start)" if view.can_start else ""))
            continue
        print(f"\n{name}  {home_short(root)}")
        if view.due:
            print(f"  Due (overdue or within {rm.DUE_SOON_DAYS} days):")
            print("\n".join("  " + line for line in view.due))
        if verifying:
            print("  Awaiting your acceptance:")
            print("\n".join(f"    {b.slug}: {b.title}" for b in verifying))
        if active:
            print("  In progress:")
            print("\n".join(f"    {b.slug}: {b.title}"
                            + (f", blocked: {b.get('blocked')}" if b.get("blocked") else "")
                            for b in active))
        if view.can_start:
            print(f"  Ready to start: {len(view.can_start)}")
        if report.errors:
            failed = True
            print(f"  {len(report.errors)} error(s); run the roadmap skill's `check` in the repository")
    if quiet:
        print("\nNothing needs attention: " + ", ".join(quiet))
    if not roots:
        print("\nNo roadmaps found.")
    for note in notes:
        print("note: " + note)
    for problem in problems:
        print("error: " + problem)
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main())
