"""Shared filesystem metadata, sizing and report helpers."""

import argparse
from collections import Counter
import ctypes
import fnmatch
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

import output
from lib.file_sizes import directory_entries


# sys/stat.h: a dataless object has no locally materialized contents.
SF_DATALESS = 0x40000000


def cloud_only(info):
    return bool(info.st_flags & SF_DATALESS)


def prevent_cloud_downloads():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.setiopolicy_np.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    libc.setiopolicy_np.restype = ctypes.c_int
    # sys/resource.h: VFS_MATERIALIZE_DATALESS_FILES, PROCESS, OFF.
    if libc.setiopolicy_np(3, 0, 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, f"could not disable automatic cloud downloads: {os.strerror(code)}")


class CloudOnlyDirectoryError(OSError):
    pass


def age_arg(value):
    match = re.fullmatch(r"(\d+)([mhdw])", value)
    if not match:
        raise argparse.ArgumentTypeError("use an age such as 30m, 24h, 7d or 2w")
    return int(match[1]) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]


def display(value):
    text = str(value)
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def human_size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def clean_targets():
    library = Path(__file__).resolve().parent / "clean-targets.sh"
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

    def update(self, folder, count, *, phase="Scanning", unit="entries inspected"):
        if not self.enabled:
            return
        now = time.monotonic()
        if now < self.next_update:
            return
        self.next_update = now + 0.2
        text = f"{phase}: {count:,} {unit} | {display(folder.relative_to(self.root))}"
        width = max(1, shutil.get_terminal_size().columns - 1)
        if len(text) > width:
            text = text[:max(0, width - 3)] + "..."[:width]
        output.progress(text)
        self.shown = True

    def close(self):
        if self.shown:
            output.progress_done()
            self.shown = False


def latest_mtime(path, *, excludes=(), clean=False, progress=None, local_only=False):
    """Inspect all included descendants; unreadable entries fail rather than appear old."""
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode) and cloud_only(info):
        raise CloudOnlyDirectoryError("Cloud-only folder contents were not inspected")
    newest = info.st_mtime
    targets = clean_targets() if clean else {}
    pending = [path] if stat.S_ISDIR(info.st_mode) else []
    inspected = 0
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as children:
            for child in children:
                relative = Path(child.path).relative_to(path).as_posix()
                if any(fnmatch.fnmatchcase(child.name, pattern)
                       or fnmatch.fnmatchcase(relative, pattern) for pattern in excludes):
                    continue
                info = child.stat(follow_symlinks=False)
                if local_only and cloud_only(info):
                    continue
                kind = "d" if stat.S_ISDIR(info.st_mode) else "f"
                if any(fnmatch.fnmatchcase(child.name, pattern) for pattern in targets.get(kind, [])):
                    continue
                newest = max(newest, info.st_mtime)
                inspected += 1
                if progress:
                    progress.update(folder, inspected)
                if stat.S_ISDIR(info.st_mode) and cloud_only(info):
                    raise CloudOnlyDirectoryError("Cloud-only folder contents were not inspected")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(child.path))
    return newest


def scan_sizes(root, recursive, direct, excludes, *, logical=False, clean=False, warnings=None,
               progress=None, include_root=False, local_only=False, cloud_paths=None,
               _directory_reader=None):
    """Keep only folder totals; macOS supplies names, types and sizes in batches."""
    warnings = warnings if warnings is not None else {}
    targets = clean_targets() if clean else {}
    pending = [("", 0)]
    totals = {}
    inspected = 0
    root_bytes = 0
    directory_reader = _directory_reader or directory_entries
    while pending:
        relative_folder, depth = pending.pop()
        folder = root / relative_folder
        subtotal = 0
        try:
            if cloud_only(folder.lstat()):
                if cloud_paths is not None:
                    cloud_paths.add(Path(relative_folder or "."))
                warnings[folder] = CloudOnlyDirectoryError("Cloud-only folder contents were not inspected")
                continue
            if progress:
                progress.update(folder, inspected)
            for name, kind, logical_size, allocated_size, error in directory_reader(folder):
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
                if local_only:
                    try:
                        if cloud_only((root / relative).lstat()):
                            continue
                    except PermissionError as exc:
                        warnings[root / relative] = exc
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
        else:
            root_bytes = subtotal
    if not direct:
        for folder in sorted(totals, key=lambda name: name.count("/"), reverse=True):
            parent = folder.rpartition("/")[0]
            if parent:
                totals[parent] += totals[folder]
    if include_root:
        return {Path("."): root_bytes + sum(size for name, size in totals.items() if "/" not in name)}
    return {Path(name): size for name, size in totals.items() if recursive or "/" not in name}


def cache_locations():
    home = Path.home()
    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME", str(home / ".cache")))
    corepack = Path(os.environ.get("COREPACK_HOME", xdg_cache / "node/corepack"))
    cargo = Path(os.environ.get("CARGO_HOME", home / ".cargo"))
    mise_data = Path(os.environ.get("MISE_DATA_DIR", Path(os.environ.get(
        "XDG_DATA_HOME", home / ".local/share")) / "mise"))
    return [
        ("npm", home / ".npm"),
        ("pnpm store", home / "Library/pnpm/store"),
        ("pnpm cache", Path(os.environ.get("XDG_CACHE_HOME", home / "Library/Caches")) / "pnpm"),
        ("Bun", home / ".bun/install/cache"),
        ("Homebrew", home / "Library/Caches/Homebrew"),
        ("pip", home / "Library/Caches/pip"),
        ("uv", xdg_cache / "uv"),
        ("Go build", home / "Library/Caches/go-build"),
        ("Go modules", home / "go/pkg/mod"),
        ("Gradle", home / ".gradle/caches"),
        ("Corepack tools", corepack / "v1"),
        ("mise cache", Path(os.environ.get("MISE_CACHE_DIR", Path(os.environ.get(
            "XDG_CACHE_HOME", home / "Library/Caches")) / "mise"))),
        ("mise downloads", mise_data / "downloads"),
        ("Cargo registry", cargo / "registry"),
        ("Cargo Git", cargo / "git"),
        ("Playwright", home / "Library/Caches/ms-playwright"),
        ("Xcode DerivedData", home / "Library/Developer/Xcode/DerivedData"),
    ]


def report_warnings(warnings, root, verbose):
    if not warnings:
        return
    output.section("Warnings")
    noun = "entry" if len(warnings) == 1 else "entries"
    output.row("warn", "Incomplete report",
               f"{len(warnings)} {noun} could not be inspected; totals may be lower.")
    if verbose:
        for path, exc in sorted(warnings.items()):
            name = path.relative_to(root) if path.is_relative_to(root) else path
            output.row("warn", display(name), display(exc.strerror or str(exc)))
    else:
        for reason, count in sorted(Counter(exc.strerror or str(exc) for exc in warnings.values()).items()):
            output.note(f"{count}: {display(reason)}")
        output.note(output.paint("Use --verbose for paths.", "dim"))
