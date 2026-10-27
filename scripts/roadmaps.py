#!/usr/bin/env python3
"""Show what needs attention across roadmaps kept with the roadmap skill.

Each PATH is a repository or a folder whose direct subfolders are repositories.
Without PATHs, they are read one per line from ~/.config/dotfiles/roadmap-paths
(`#` starts a comment, `~` is expanded). Only roadmaps with the skill's
generated-list markers in roadmap.md or roadmaps.md (case-insensitive), at the
repository root or in docs/, tasks/ or planning/, are included. Briefs live in
roadmap/ beside the index, or at the repository root. Read-only; exits 1 when a
roadmap has errors or cannot be read, or no paths are configured.
Use --check to print detailed validation results instead of the overview;
missing paths and paths without managed roadmaps then also exit 1.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import os
import shlex
import sys
from pathlib import Path
from typing import List, Optional

SKILL_SCRIPT = Path(__file__).resolve().parents[1] / "agents/skills/roadmap/scripts/roadmap.py"
_spec = importlib.util.spec_from_file_location("roadmap_skill", SKILL_SCRIPT)
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import output  # noqa: E402

INDEX_FOLDERS = ("docs", "tasks", "planning")


def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "dotfiles", "roadmap-paths")


def home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def conforming(index: str, problems: List[str]) -> bool:
    if not os.path.isfile(index):
        return False
    try:
        with open(index, encoding="utf-8") as handle:
            return rm.START in handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{home_short(index)}: cannot read ({exc.__class__.__name__})")
        return False


def repository_roadmaps(root: str, problems: List[str],
                       include_nested: bool = True) -> List[argparse.Namespace]:
    roadmaps = []
    seen = set()
    try:
        root_names = sorted(os.listdir(root))
    except OSError as exc:
        problems.append(f"{home_short(root)}: cannot list ({exc.__class__.__name__})")
        return []
    folders = [""] + [name for name in root_names
                      if include_nested and name.lower() in INDEX_FOLDERS]
    root_briefs = os.path.join(root, next(
        (n for n in root_names if n.lower() == "roadmap"
         and os.path.isdir(os.path.join(root, n))), "roadmap"))
    for folder in folders:
        directory = os.path.join(root, folder)
        if not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError as exc:
            problems.append(f"{home_short(directory)}: cannot list ({exc.__class__.__name__})")
            continue
        for name in names:
            if name.lower() not in ("roadmap.md", "roadmaps.md"):
                continue
            index = os.path.join(directory, name)
            identity = os.path.realpath(index)
            if identity in seen:
                continue
            if not conforming(index, problems):
                continue
            seen.add(identity)
            brief_name = next((n for n in names
                               if n.lower() == "roadmap"
                               and os.path.isdir(os.path.join(directory, n))), "roadmap")
            brief_dir = os.path.join(directory, brief_name)
            if folder and not os.path.isdir(brief_dir) and os.path.isdir(root_briefs):
                brief_dir = root_briefs
            roadmaps.append(argparse.Namespace(
                root=root, index=os.path.relpath(index, root),
                dir=os.path.relpath(brief_dir, root), strict=False))
    return roadmaps


def find_roadmaps(folder: str, problems: List[str]) -> List[argparse.Namespace]:
    """`folder` itself when it holds a roadmap, else its direct non-hidden
    subdirectories that do."""
    roadmaps = repository_roadmaps(
        folder, problems, include_nested=os.path.exists(os.path.join(folder, ".git")))
    if roadmaps:
        return roadmaps
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        problems.append(f"{home_short(folder)}: cannot list ({exc.__class__.__name__})")
        return []
    return [roadmap for n in names
            if not n.startswith(".") and os.path.isdir(os.path.join(folder, n))
            for roadmap in repository_roadmaps(os.path.join(folder, n), problems)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="roadmaps", description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="a repository, or a folder whose direct subfolders are repositories")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="treat this date as today")
    parser.add_argument("--check", action="store_true",
                        help="report detailed validation results without changing files")
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

    roadmaps: List[argparse.Namespace] = []
    seen = set()
    notes: List[str] = []
    problems: List[str] = []
    for entry in entries:
        folder = os.path.abspath(os.path.expanduser(entry))
        if not os.path.isdir(folder):
            (problems if args.check else notes).append(f"{entry}: not a directory")
            continue
        output.progress(f"Finding roadmaps in {home_short(folder)}…")
        try:
            found = find_roadmaps(folder, problems)
        finally:
            output.progress_done()
        if args.check and not found:
            problems.append(f"{home_short(folder)}: no managed roadmap found")
        for repo in found:
            index = os.path.realpath(os.path.join(repo.root, repo.index))
            if index not in seen:
                seen.add(index)
                roadmaps.append(repo)

    output.context(today.isoformat())
    quiet: List[str] = []
    failed = False
    for repo in roadmaps:
        root = repo.root
        try:
            output.progress(f"Checking {home_short(root)}…")
            try:
                report, briefs = rm.check_report(repo)
            finally:
                output.progress_done()
        except (OSError, UnicodeDecodeError) as exc:
            failed = True
            output.section(f"{os.path.basename(root)}  {home_short(root)}")
            print(f"  {output.mark('fail')} cannot read the roadmap: {exc}")
            continue
        if args.check:
            output.section(f"{os.path.basename(root)}  {home_short(root)}")
            for error in report.errors:
                print(f"  {output.mark('fail')} {error}")
            for warning in report.warnings:
                print(f"  {output.mark('warn')} {warning}")
            if report.errors:
                failed = True
            else:
                open_count = sum(not brief.archived for brief in briefs)
                print(f"  {output.mark('ok')} Valid roadmap: {repo.index} "
                      f"({open_count} open, {len(briefs) - open_count} archived)")
            continue
        view = rm.Overview(briefs, today)
        verifying = [b for b in view.now if b.status == "verifying"]
        active = [b for b in view.now if b.status != "verifying"]
        name = os.path.basename(root)
        if not (view.due or verifying or active or report.errors):
            quiet.append(name + (f" ({len(view.can_start)} ready to start)" if view.can_start else ""))
            continue
        output.section(f"{name}  {home_short(root)}")
        if view.due:
            output.subsection(f"Due (overdue or within {rm.DUE_SOON_DAYS} days)")
            print("\n".join("  " + line for line in view.due))
        if verifying:
            output.subsection("Awaiting your acceptance")
            print("\n".join(f"    {b.slug}: {b.title}" for b in verifying))
        if active:
            output.subsection("In progress")
            print("\n".join(f"    {b.slug}: {b.title}"
                            + (f", blocked: {b.get('blocked')}" if b.get("blocked") else "")
                            for b in active))
        if view.can_start or report.errors:
            print()
        if view.can_start:
            print(f"  {output.mark('info')} Ready to start: {len(view.can_start)}")
        if report.errors:
            failed = True
            print(f"  {output.mark('fail')} {len(report.errors)} error(s); "
                  f"run roadmaps --check {shlex.quote(root)}")
    if quiet:
        print(f"\n  {output.mark('ok')} Nothing needs attention: " + ", ".join(quiet))
    if not roadmaps:
        print()
        output.note("No roadmaps found.")
        if args.check:
            failed = True
    if notes or problems:
        print()
    for note in notes:
        print(f"  {output.mark('info')} note: {note}")
    for problem in problems:
        print(f"  {output.mark('fail')} error: {problem}")
    return 1 if failed or problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
