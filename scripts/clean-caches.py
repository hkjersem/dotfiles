#!/usr/bin/env python3
"""Preview global developer caches and explicitly select cleaners to run."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from files import ScanProgress, display, human_size, output, scan_sizes  # noqa: E402


@dataclass
class Cleaner:
    name: str
    tool: str
    query: tuple[str, ...]
    command: tuple[str, ...]
    cost: str
    deep: bool = False


CLEANERS = (
    Cleaner("npm", "npm", ("config", "get", "cache"), ("cache", "clean", "--force"), "downloads"),
    Cleaner("pnpm", "pnpm", ("store", "path"), ("store", "prune"), "unreferenced packages"),
    Cleaner("brew", "brew", ("--cache",), ("cleanup",), "old versions/downloads"),
    Cleaner("bun", "bun", ("pm", "cache"), ("pm", "cache", "rm"), "downloads"),
    Cleaner("yarn", "yarn", (), ("cache", "clean"), "downloads"),
    Cleaner("pip", "pip3", ("cache", "dir"), ("cache", "purge"), "downloads"),
    Cleaner("uv", "uv", ("cache", "dir"), ("cache", "clean"), "downloads/builds"),
    Cleaner("go-build", "go", ("env", "GOCACHE"), ("clean", "-cache"), "rebuild"),
    Cleaner("go-modules", "go", ("env", "GOMODCACHE"), ("clean", "-modcache"), "downloads", True),
    Cleaner("gradle", "", (), (), "downloads/rebuild", True),
    Cleaner("playwright", "", (), (), "browser downloads", True),
    Cleaner("xcode", "", (), (), "rebuild/reindex"),
)
BY_NAME = {cleaner.name: cleaner for cleaner in CLEANERS}


def query(tool, args):
    result = subprocess.run([tool, *args], cwd=Path.home(), capture_output=True, text=True)
    if result.returncode:
        raise OSError(f"{tool} {' '.join(args)} failed: {display(result.stderr.strip() or result.stdout.strip())}")
    return result.stdout.strip()


def cache_path(cleaner):
    home = Path.home()
    if cleaner.tool:
        if not shutil.which(cleaner.tool):
            raise OSError(f"{cleaner.tool} is not installed")
        if cleaner.name == "yarn":
            major = query("yarn", ("--version",)).split(".", 1)[0]
            value = query("yarn", ("--silent", "cache", "dir") if major == "1"
                          else ("config", "get", "cacheFolder"))
        else:
            value = query(cleaner.tool, cleaner.query)
    elif cleaner.name == "gradle":
        value = str(Path(os.environ.get("GRADLE_USER_HOME", home / ".gradle")) / "caches")
    elif cleaner.name == "playwright":
        value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(home / "Library/Caches/ms-playwright"))
    else:
        value = str(home / "Library/Developer/Xcode/DerivedData")
    path = Path(value).expanduser()
    if not value or "\n" in value or not path.is_absolute():
        raise OSError(f"{cleaner.name} did not provide an absolute global cache path: {display(value)}")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise OSError(f"refusing symlink cache path: {display(parent)}")
    resolved = path.resolve()
    # Never accept a tool/configuration pointing at a broad directory.
    protected = (home.resolve(), Path.cwd().resolve())
    if resolved == Path("/") or any(resolved == parent or resolved in parent.parents for parent in protected):
        raise OSError(f"refusing broad cache path: {display(path)}")
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    if not path.is_dir():
        raise OSError(f"not a cache directory: {display(path)}")
    return path


def parser():
    result = argparse.ArgumentParser(
        prog="clean_caches", description=__doc__,
        epilog="Runs globally from HOME, not in the current project. Project artifacts: wipe_clean.\n"
               "Cleaners: " + ", ".join(BY_NAME),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument("--only", metavar="NAMES", help="comma-separated cleaners; required unless --dry-run")
    result.add_argument("--deep", action="store_true", help="allow Gradle, Go modules and Playwright browser cleanup")
    result.add_argument("-n", "--dry-run", action="store_true", help="preview without prompting or cleaning")
    return result


def confirm_cleanup():
    library = Path(__file__).resolve().parent / "package-manager/_update-lib.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1" || exit; confirm_apply "Clean these global caches?" false false',
         "clean-caches-confirm", str(library)],
    )
    if result.returncode not in (0, 1):
        raise OSError(f"confirmation failed with status {result.returncode}")
    return result.returncode == 0


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    names = list(dict.fromkeys(args.only.split(","))) if args.only is not None else [
        cleaner.name for cleaner in CLEANERS if args.deep or not cleaner.deep]
    if any(name not in BY_NAME for name in names):
        cli.error("--only must contain cleaner names from: " + ", ".join(BY_NAME))
    if not args.dry_run and args.only is None:
        cli.error("explicit --only selection is required unless --dry-run is given")
    if any(BY_NAME[name].deep for name in names) and not args.deep:
        cli.error("Gradle, Go modules and Playwright require --deep")
    if args.dry_run:
        output.dry_run()
    output.context("Global caches")
    output.section("Cache cleanup plan")
    progress = ScanProgress(Path("/"), True)
    rows = []
    failures = 0
    try:
        for name in names:
            cleaner = BY_NAME[name]
            if not args.only and cleaner.tool and not shutil.which(cleaner.tool):
                continue
            try:
                path = cache_path(cleaner)
                try:
                    path.lstat()
                except FileNotFoundError:
                    output.row("info", name, "No cache directory")
                    continue
                warnings = {}
                size = scan_sizes(path, False, False, [], include_root=True, warnings=warnings,
                                  progress=progress)[Path(".")]
                if warnings:
                    raise OSError(f"cache size incomplete: {display(next(iter(warnings)))}")
                rows.append((cleaner, path))
                output.row("info", name, f"{human_size(size)} | {cleaner.cost}")
                output.note(display(path))
            except OSError as exc:
                failures += 1
                output.row("fail", name, display(exc))
    finally:
        progress.close()
    if failures:
        if not args.dry_run:
            output.note("Nothing cleaned: resolve the failed checks first.")
        return 1
    if args.dry_run or not rows:
        return 0
    if not confirm_cleanup():
        return 0
    output.section("Cleaning")
    for cleaner, path in rows:
        try:
            if cache_path(cleaner) != path:
                raise OSError("configured cache location changed after preview")
            before = shutil.disk_usage(path.parent).free
            if cleaner.command:
                result = subprocess.run([cleaner.tool, *cleaner.command], cwd=Path.home())
                if result.returncode:
                    raise OSError(f"cleaner exited with status {result.returncode}")
            else:
                shutil.rmtree(path)
            after = shutil.disk_usage(path.parent).free
            change = after - before
            output.row("ok", cleaner.name, "Cleaned")
            output.note(f"Observed available-space change: {'+' if change >= 0 else '-'}"
                        f"{human_size(abs(change))}")
        except OSError as exc:
            failures += 1
            output.row("fail", cleaner.name, display(exc))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        sys.exit(130)
