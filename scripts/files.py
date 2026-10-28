#!/usr/bin/env python3
"""Read-only filesystem reports for the files shell command."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import output  # noqa: E402
from file_sizes import directory_entries  # noqa: E402


COMMANDS = {
    "count": "Count files, folders, symlinks and other entries",
    "large": "List files by size, largest first",
    "changed": "List files by modification time, newest first",
    "sizes": "Show allocated file bytes per child folder, including its subfolders",
    "empty": "Find zero-byte files and truly empty folders",
    "types": "Group files by extension, with counts and bytes",
    "duplicates": "Find nonempty files with identical content; never delete",
}
MONTHS = (
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
)
SIZE_CACHE_VERSION = 2
SIZE_CACHE_TTL = 24 * 60 * 60


@dataclass
class Entry:
    path: Path
    relative: Path
    info: os.stat_result


def size_arg(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)(?:i?B)?", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError("use bytes or a size such as 100M or 1.5GiB")
    return int(float(match[1]) * 1024 ** ("", "K", "M", "G", "T").index(match[2].upper()))


def age_arg(value):
    match = re.fullmatch(r"(\d+)([mhdw])", value)
    if not match:
        raise argparse.ArgumentTypeError("use an age such as 30m, 24h, 7d or 2w")
    return int(match[1]) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]


def limit_arg(value):
    if not value.isdecimal():
        raise argparse.ArgumentTypeError("use a non-negative integer (0 shows all)")
    return int(value)


def parser():
    result = argparse.ArgumentParser(
        prog="files", usage="files <command> [options] [path]",
        allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read-only folder reports. Defaults to the current folder; includes hidden entries.\n"
                    "Never follows symlinks. No Git or generated-file exclusions are implicit.",
        epilog="Options (after the command; see files <command> --help):\n"
               "  -r, --recursive  Descend into subfolders\n"
               "  --clean  Exclude wipe_clean's shared generated-artifact targets\n"
               "  --clean-git  Exclude generated artifacts and .git entries at every depth\n"
               "  --exclude GLOB  Skip a name or relative-path glob; repeatable\n"
               "  --limit N  Show at most N rows/groups (default 20; 0 means all)\n"
               "  --min SIZE  Minimum file size for large (bytes, K, M, G, T; base 1024)\n"
               "  --direct  Count direct files only for sizes (default includes all subfolders)\n"
               "  --logical  Count logical file bytes instead of allocated bytes for sizes\n"
               "  --cached  Reuse the last sizes snapshot (expires after 24 hours); never rescan\n"
               "  --since AGE  Modification age for changed (m, h, d, w)\n",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name, description in COMMANDS.items():
        command = commands.add_parser(name, help=description, description=description, allow_abbrev=False)
        command.add_argument("path", nargs="?", default=".", help="folder to inspect (default: .)")
        command.add_argument("-r", "--recursive", action="store_true",
                             help="also list nested folders" if name == "sizes" else "descend into subfolders")
        command.add_argument("--clean", action="store_true",
                             help="exclude wipe_clean's shared generated-artifact targets")
        command.add_argument("--clean-git", action="store_true",
                             help="exclude generated artifacts and .git entries at every depth")
        command.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                             help="skip a name or relative-path glob; repeatable")
        if name != "count":
            command.add_argument("--limit", type=limit_arg, default=20, metavar="N",
                                 help="maximum rows/groups (default: 20; 0 shows all)")
        if name == "large":
            command.add_argument("--min", type=size_arg, default=0, metavar="SIZE",
                                 help="minimum bytes, or e.g. 100M or 1.5GiB")
        if name == "changed":
            command.add_argument("--since", type=age_arg, metavar="AGE", help="modified within e.g. 24h or 7d")
        if name == "sizes":
            command.add_argument("--direct", action="store_true",
                                 help="count only files directly inside each listed folder")
            command.add_argument("--logical", action="store_true",
                                 help="count logical file bytes instead of allocated bytes")
            command.add_argument("--cached", action="store_true",
                                 help="reuse the last sizes snapshot, up to 24 hours old; never rescan")
    return result


def display(value):
    text = str(value)
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def human_size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def clean_targets():
    library = Path(__file__).resolve().parent / "lib" / "clean-targets.sh"
    result = subprocess.run(
        ["/bin/bash", "-c",
         'source "$1" || exit\n'
         'printf "d\\0%s\\0" "${CLEAN_DIR_TARGETS[@]}"\n'
         'printf "f\\0%s\\0" "${CLEAN_FILE_TARGETS[@]}"',
         "files-clean-targets", str(library)],
        capture_output=True,
    )
    if result.returncode:
        raise OSError(f"could not load shared clean targets: {os.fsdecode(result.stderr).strip()}")
    records = os.fsdecode(result.stdout).split("\0")[:-1]
    return {
        kind: [pattern for tag, pattern in zip(records[::2], records[1::2]) if tag == kind]
        for kind in ("d", "f")
    }


class ScanProgress:
    def __init__(self, root, enabled):
        self.root = root
        self.enabled = enabled and sys.stdout.isatty() and sys.stderr.isatty()
        self.next_update = time.monotonic() + 2
        self.shown = False

    def update(self, folder, count):
        if not self.enabled:
            return
        now = time.monotonic()
        if now < self.next_update:
            return
        self.next_update = now + 0.2
        text = f"Scanning: {count:,} entries inspected | {display(folder.relative_to(self.root))}"
        width = max(1, shutil.get_terminal_size().columns - 1)
        if len(text) > width:
            text = text[:max(0, width - 3)] + "..."[:width]
        output.progress(text)
        self.shown = True

    def close(self):
        if self.shown:
            output.progress_done()


def scan(root, recursive, excludes, *, clean=False, warnings=None, progress=None):
    entries = []
    inspected = 0
    warnings = warnings if warnings is not None else {}
    targets = clean_targets() if clean else {}
    pending = [root]
    while pending:
        folder = pending.pop()
        if progress:
            progress.update(folder, inspected)
        try:
            with os.scandir(folder) as children:
                for child in children:
                    inspected += 1
                    if progress:
                        progress.update(folder, inspected)
                    relative = Path(child.path).relative_to(root)
                    if any(fnmatch.fnmatchcase(child.name, pattern)
                           or fnmatch.fnmatchcase(relative.as_posix(), pattern) for pattern in excludes):
                        continue
                    try:
                        info = child.stat(follow_symlinks=False)
                    except PermissionError as exc:
                        warnings[Path(child.path)] = exc
                        continue
                    kind = "d" if stat.S_ISDIR(info.st_mode) else "f" if stat.S_ISREG(info.st_mode) else None
                    if any(fnmatch.fnmatchcase(child.name, pattern) for pattern in targets.get(kind, [])):
                        continue
                    entries.append(Entry(Path(child.path), relative, info))
                    if stat.S_ISDIR(info.st_mode) and recursive:
                        pending.append(Path(child.path))
        except PermissionError as exc:
            if folder == root:
                raise
            warnings[folder] = exc
    return entries


def scan_sizes(root, recursive, direct, excludes, *, logical=False, clean=False, warnings=None, progress=None):
    """Keep only folder totals; macOS supplies names, types and sizes in batches."""
    warnings = warnings if warnings is not None else {}
    targets = clean_targets() if clean else {}
    pending = [("", 0)]
    totals = {}
    inspected = 0
    while pending:
        relative_folder, depth = pending.pop()
        folder = root / relative_folder
        subtotal = 0
        try:
            if progress:
                progress.update(folder, inspected)
            for name, kind, logical_size, allocated_size, error in directory_entries(folder):
                inspected += 1
                if progress:
                    progress.update(folder, inspected)
                relative = f"{relative_folder}/{name}" if relative_folder else name
                if any(fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(relative, pattern)
                       for pattern in excludes):
                    continue
                if error:
                    entry_error = OSError(error, os.strerror(error), str(root / relative))
                    if not isinstance(entry_error, PermissionError):
                        raise entry_error
                    warnings[root / relative] = entry_error
                    continue
                if any(fnmatch.fnmatchcase(name, pattern) for pattern in targets.get(kind, [])):
                    continue
                if kind == "d":
                    totals[relative] = 0
                    if not direct or recursive or depth == 0:
                        pending.append((relative, depth + 1))
                elif kind == "f":
                    subtotal += logical_size if logical else allocated_size
        except PermissionError as exc:
            if not relative_folder:
                raise
            warnings[folder] = exc
        if relative_folder:
            totals[relative_folder] = subtotal
    if not direct:
        for folder in sorted(totals, key=lambda name: name.count("/"), reverse=True):
            parent = folder.rpartition("/")[0]
            if parent:
                totals[parent] += totals[folder]
    return {Path(name): size for name, size in totals.items() if recursive or "/" not in name}


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def digest(entry):
    # Refuse symlink swaps and non-regular replacements, and detect writes during hashing.
    fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or signature(before) != signature(entry.info):
            raise OSError(f"file changed during scan: {display(entry.path)}")
        value = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
        if signature(os.fstat(source.fileno())) != signature(before):
            raise OSError(f"file changed during hashing: {display(entry.path)}")
    return value.digest()


def duplicate_groups(files, warnings=None):
    warnings = warnings if warnings is not None else {}
    by_size = defaultdict(list)
    seen = set()
    for entry in sorted(files, key=lambda entry: entry.relative.as_posix()):
        inode = entry.info.st_dev, entry.info.st_ino
        if entry.info.st_size and inode not in seen:
            by_size[entry.info.st_size].append(entry)
            seen.add(inode)
    groups = []
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue
        hashes = defaultdict(list)
        for entry in candidates:
            try:
                hashes[digest(entry)].append(entry)
            except PermissionError as exc:
                warnings[entry.path] = exc
        groups.extend(group for group in hashes.values() if len(group) > 1)
    # Check again after all reads, so a file changed after its hash is not reported as a match.
    verified = []
    for group in groups:
        readable = []
        for entry in group:
            try:
                if signature(entry.path.lstat()) != signature(entry.info):
                    raise OSError(f"file changed during scan: {display(entry.path)}")
                readable.append(entry)
            except PermissionError as exc:
                warnings[entry.path] = exc
        if len(readable) > 1:
            verified.append(readable)
    return sorted(verified, key=lambda group: (-group[0].info.st_size, group[0].relative.as_posix()))


def size_cache_path(root, args):
    settings = [SIZE_CACHE_VERSION, str(root), args.recursive, args.direct, args.logical, args.clean or args.clean_git,
                args.clean_git, sorted(set(args.exclude))]
    if args.clean or args.clean_git:
        settings.append((Path(__file__).resolve().parent / "lib" / "clean-targets.sh").read_text())
    key = hashlib.sha256(json.dumps(settings).encode()).hexdigest()
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "dotfiles" / "files-sizes" / f"{key}.json"


def save_size_cache(path, totals, warnings):
    snapshot = {
        "created": time.time(),
        "totals": {str(name): size for name, size in totals.items()},
        "warnings": {str(name): error.strerror or str(error) for name, error in warnings.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(snapshot, file)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def clean_size_cache(directory):
    try:
        children = directory.iterdir()
        for path in children:
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and time.time() - info.st_mtime >= SIZE_CACHE_TTL:
                    path.unlink()
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        return


def load_size_cache(path):
    try:
        snapshot = json.loads(path.read_text())
        created = snapshot["created"]
        totals = snapshot["totals"]
        warnings = snapshot["warnings"]
        if (type(created) not in (int, float) or not 0 <= time.time() - created < SIZE_CACHE_TTL
                or not isinstance(totals, dict) or not isinstance(warnings, dict)
                or any(not isinstance(name, str) or type(size) is not int or size < 0
                       for name, size in totals.items())
                or any(not isinstance(name, str) or not isinstance(message, str)
                       for name, message in warnings.items())):
            raise ValueError("invalid or expired snapshot")
        return ({Path(name): size for name, size in totals.items()},
                {Path(name): PermissionError(message) for name, message in warnings.items()}, created)
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        raise OSError("no usable sizes cache (missing, expired or invalid); run without --cached to refresh") from exc


def report(args):
    root = Path(os.path.abspath(os.path.expanduser(args.path)))
    sizes = args.command == "sizes"
    cached = sizes and args.cached
    if not cached and (root.is_symlink() or not root.is_dir()):
        raise OSError(f"not a real directory (symlink roots are not followed): {display(root)}")
    excludes = args.exclude + ([".git"] if args.clean_git else [])
    warnings = {}
    cache_path = size_cache_path(root, args) if sizes else None
    if sizes:
        clean_size_cache(cache_path.parent)
    progress = ScanProgress(root, sizes)
    try:
        if cached:
            totals, warnings, created = load_size_cache(cache_path)
            entries = []
        elif sizes:
            totals = scan_sizes(root, args.recursive, args.direct, excludes, logical=args.logical,
                                clean=args.clean or args.clean_git, warnings=warnings, progress=progress)
            save_size_cache(cache_path, totals, warnings)
            entries = []
        else:
            entries = scan(root, args.recursive, excludes,
                           clean=args.clean or args.clean_git, warnings=warnings, progress=progress)
    finally:
        progress.close()
    files = [entry for entry in entries if stat.S_ISREG(entry.info.st_mode)]
    folders = [entry for entry in entries if stat.S_ISDIR(entry.info.st_mode)]
    rows = []
    groups = []
    if args.command == "count":
        links = sum(stat.S_ISLNK(entry.info.st_mode) for entry in entries)
        rows = [("Files", len(files)), ("Folders", len(folders)), ("Symlinks", links),
                ("Other", len(entries) - len(files) - len(folders) - links)]
    elif args.command == "large":
        selected = sorted((entry for entry in files if entry.info.st_size >= args.min),
                          key=lambda entry: (-entry.info.st_size, entry.relative.as_posix()))
        rows = [(human_size(entry.info.st_size), display(entry.relative)) for entry in selected]
    elif args.command == "changed":
        cutoff = time.time() - args.since if args.since is not None else None
        for entry in sorted(files, key=lambda entry: (-entry.info.st_mtime_ns, entry.relative.as_posix())):
            if cutoff is not None and entry.info.st_mtime < cutoff:
                continue
            stamp = datetime.fromtimestamp(entry.info.st_mtime)
            formatted = f"{stamp.day}. {MONTHS[stamp.month - 1]} {stamp.year}, kl. {stamp:%H.%M.%S}"
            rows.append((formatted, display(entry.relative)))
    elif args.command == "sizes":
        incomplete = set()
        for path in warnings:
            relative = path.relative_to(root)
            incomplete.add(relative)
            incomplete.update([relative.parent] if args.direct else relative.parents)
        rows = [("Unknown" if name in incomplete else human_size(size),
                 output.listing_name(display(name), "folder")) for name, size in
                sorted(totals.items(), key=lambda item: (-item[1], item[0]))
                if args.recursive or len(name.parts) == 1]
    elif args.command == "empty":
        rows = [("File", display(entry.relative)) for entry in files if not entry.info.st_size]
        for entry in folders:
            if entry.path in warnings:
                continue
            try:
                with os.scandir(entry.path) as children:
                    if next(children, None) is None:
                        rows.append(("Folder", display(entry.relative)))
            except PermissionError as exc:
                warnings[entry.path] = exc
        rows.sort(key=lambda row: row[1])
        rows = [(kind, output.listing_name(name, kind.lower())) for kind, name in rows]
    elif args.command == "types":
        totals = defaultdict(lambda: [0, 0])
        for entry in files:
            key = entry.relative.suffix.lower() or "(no extension)"
            totals[key][0] += 1
            totals[key][1] += entry.info.st_size
        rows = [(f"{count:>6}  {human_size(size):>12}", display(extension))
                for extension, (count, size) in sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))]
    elif args.command == "duplicates":
        groups = duplicate_groups(files, warnings)

    scope = ("all folders" if args.recursive else "child folders") if sizes else (
        "recursive" if args.recursive else "non-recursive")
    context = [display(root), scope]
    if cached:
        age = int(time.time() - created)
        context.append(f"cached: {age // 3600}h {(age % 3600) // 60}m {age % 60}s old")
    if args.clean or args.clean_git:
        context.append("clean: generated artifacts excluded")
    if args.clean_git:
        context.append("clean-git: .git entries excluded")
    if warnings:
        context.append("incomplete")
    output.context(*context)
    output.section(args.command.capitalize())
    if args.command == "sizes":
        output.note(("Logical file bytes; " if args.logical else "Allocated file bytes; ") +
                    ("direct files only." if args.direct else "includes all subfolders."))
        if args.logical:
            output.note("Sparse files show their full length; hard-linked paths count separately.")
        else:
            output.note("Sparse files count allocated data; shared APFS blocks can be counted more than once.")
    if args.command == "duplicates":
        output.note("Nonempty files only; hard links count once. No files are changed.")
        reclaimable = 0
        for group in groups:
            # Keep a hard-linked inode if present; deleting other names of it would not free its bytes.
            ordered = sorted(group, key=lambda entry: (entry.info.st_nlink == 1, entry.relative.as_posix()))
            reclaimable += sum(entry.info.st_size for entry in ordered[1:] if entry.info.st_nlink == 1)
        output.row("info", f"{len(groups)} duplicate groups", f"potential saving: {human_size(reclaimable)}")
        output.note("Logical estimate, excluding hard-linked copies; APFS sharing may reduce actual savings.")
        for number, group in enumerate(groups[:args.limit or None], 1):
            output.subsection(f"Group {number}: {human_size(group[0].info.st_size)} each")
            for entry in group:
                output.note(display(entry.relative))
        total = len(groups)
    else:
        if args.command == "types":
            output.table_header("%6s  %12s  %s", "Files", "Bytes", "Extension")
        limit = getattr(args, "limit", 0)
        for label, detail in rows[:limit or None]:
            print(f"  {label:<12}  {detail}")
        if not rows:
            output.note("No matches.")
        total = len(rows)
    limit = getattr(args, "limit", 0)
    if limit and total > limit:
        output.note(f"Showing {limit} of {total}; use --limit 0 to show all.")
    if warnings:
        output.section("Warnings")
        output.row("warn", "Incomplete report", "Permission-denied entries were skipped; totals may be lower.")
        for path, exc in sorted(warnings.items()):
            output.row("warn", display(path.relative_to(root)), display(exc.strerror or str(exc)))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report(args)
    except OSError as exc:
        print(f"Error: {display(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
