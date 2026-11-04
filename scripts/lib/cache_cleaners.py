#!/usr/bin/env python3
"""Shared global cache discovery, previews and native cleanup."""

import argparse
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from files import ScanProgress, cache_locations, display, human_size, output, scan_sizes  # noqa: E402
from lib import command_help  # noqa: E402
from lib import orphan_caches  # noqa: E402


@dataclass
class Cleaner:
    name: str
    tool: str
    query: tuple[str, ...]
    command: tuple[str, ...]
    cost: str
    deep: bool = False
    version: str = ""
    prefix: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


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
    Cleaner("mise", "mise", ("cache", "path"), ("cache", "prune"), "stale metadata/task/environment caches"),
)
BY_NAME = {cleaner.name: cleaner for cleaner in CLEANERS}
# Playwright installs each browser as <name>-<revision>, plus these bookkeeping entries.
PLAYWRIGHT_BROWSER = re.compile(r"[a-z][a-z_-]*-\d+")
PLAYWRIGHT_EXTRAS = {".links", "__dirlock", ".DS_Store"}


def query(tool, args, environment=()):
    if Path(tool).name == "mise":
        environment = (*environment, ("MISE_CACHE_PRUNE_AGE", "0s"))
    result = subprocess.run([tool, *args], cwd=Path.home(), capture_output=True, text=True,
                            env=tool_environment(environment))
    if result.returncode:
        raise OSError(f"{tool} {' '.join(args)} failed: {display(result.stderr.strip() or result.stdout.strip())}")
    return result.stdout.strip()


def tool_environment(overrides=()):
    return {**os.environ, "GOTOOLCHAIN": "local", "GOTELEMETRY": "off", "MISE_OFFLINE": "1",
            "COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_AUTO_PIN": "0",
            "npm_config_manage_package_manager_versions": "false",
            "PNPM_CONFIG_MANAGE_PACKAGE_MANAGER_VERSIONS": "false", **dict(overrides)}


def go_executable():
    executable = shutil.which("go")
    if executable:
        return str(Path(executable).resolve())
    return mise_executable("go")


def mise_executable(tool):
    label = "Go" if tool == "go" else tool
    mise = shutil.which("mise")
    if not mise:
        return None
    try:
        versions = json.loads(query(mise, ("ls", "--installed", tool, "--json")))
    except ValueError as exc:
        raise OSError(f"mise returned invalid installed {label} JSON") from exc
    if not isinstance(versions, list):
        raise OSError(f"mise returned an invalid installed {label} list")
    if not versions:
        return None
    for version in versions:
        if (not isinstance(version, dict) or version.get("installed") is not True
                or not isinstance(version.get("version"), str)
                or not re.fullmatch(r"\d+\.\d+\.\d+", version["version"])
                or not isinstance(version.get("install_path"), str)
                or not Path(version["install_path"]).is_absolute()):
            raise OSError(f"mise returned an unsupported installed {label} record")
    selected = max(versions, key=lambda row: (
        row.get("active") is True, tuple(int(part) for part in row["version"].split("."))))
    executable = Path(selected["install_path"]) / "bin" / tool
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise OSError(f"mise {label} executable unavailable: {display(executable)}")
    return str(executable.resolve())


def bun_cleaners():
    home = Path.home()
    candidates = []
    executable = shutil.which("bun")
    if executable:
        candidates.append(Path(executable))
    candidates.append(Path(os.environ.get("BUN_INSTALL", home / ".bun")) / "bin/bun")
    candidates.extend(installation / "bin/bun" for installation in fnm_installations())
    mise = mise_executable("bun")
    if mise:
        candidates.append(Path(mise))
    cleaners = []
    seen = set()
    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            cleaners.append(replace(BY_NAME["bun"], tool=resolved))
    return cleaners


def fnm_installations():
    home = Path.home()
    root = Path(os.environ.get("FNM_DIR", home / ".local/share/fnm"))
    if "FNM_DIR" not in os.environ and not root.is_dir() and (home / ".fnm").is_dir():
        root = home / ".fnm"
    versions = root / "node-versions"
    if not versions.is_dir():
        return []
    return sorted(versions.glob("*/installation"))


def corepack_home():
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(os.environ.get("COREPACK_HOME", cache / "node/corepack"))


def package_cleaners(name):
    """Run installed npm/Yarn entries directly, never Corepack's download shim."""
    cleaner = BY_NAME[name]
    candidates = []
    executable = shutil.which(name)
    if executable:
        resolved = Path(executable).resolve()
        # Corepack shims are not package managers; discover their cached entries below.
        if "corepack" not in resolved.parts:
            candidates.append(replace(cleaner, tool=str(resolved)))
    for installation in fnm_installations():
        node = installation / "bin/node"
        entry = installation / "bin" / name
        if node.is_file() and entry.is_file():
            resolved = entry.resolve()
            if "corepack" not in resolved.parts:
                candidates.append(replace(
                    cleaner, tool=str(node.resolve()), prefix=(str(resolved),),
                    query=(str(resolved), *cleaner.query),
                    command=(str(resolved), *cleaner.command),
                    environment=(("PATH", str(node.parent) + os.pathsep + os.environ.get("PATH", "")),)))
    packages = corepack_home() / "v1" / name
    if packages.exists():
        validated_path(f"Corepack {name} packages", str(packages))
        node = shutil.which("node")
        for package in sorted(packages.iterdir()):
            if not re.fullmatch(r"\d+\.\d+\.\d+", package.name):
                continue
            validated_path(f"Corepack {name} package", str(package))
            entry = package / ("bin/npm-cli.js" if name == "npm" else
                               "bin/yarn.js" if package.name.startswith("1.") else "yarn.js")
            if any(path.is_symlink() for path in (entry, *entry.parents)) or not entry.is_file():
                raise OSError(f"invalid installed {name} entry point: {display(entry)}")
            if not node:
                raise OSError(f"node is required to run an installed {name}")
            candidates.append(replace(
                cleaner, tool=str(Path(node).resolve()), prefix=(str(entry),),
                query=(str(entry), *cleaner.query), command=(str(entry), *cleaner.command)))
    unique = {}
    for candidate in candidates:
        unique.setdefault((candidate.tool, candidate.prefix), candidate)
    return list(unique.values())


def cache_path(cleaner):
    home = Path.home()
    if cleaner.tool:
        if not shutil.which(cleaner.tool):
            raise OSError(f"{cleaner.tool} is not installed")
        if cleaner.name == "yarn":
            major = query(cleaner.tool, (*cleaner.prefix, "--version"), cleaner.environment).split(".", 1)[0]
            if major == "1":
                value = query(cleaner.tool, (*cleaner.prefix, "--silent", "cache", "dir"), cleaner.environment)
            elif major.isdigit() and int(major) >= 2:
                value = query(cleaner.tool, (*cleaner.prefix, "config", "get", "cacheFolder"),
                              cleaner.environment)
            else:
                raise OSError(f"unsupported Yarn major: {display(major)}")
        else:
            value = query(cleaner.tool, cleaner.query, cleaner.environment)
    elif cleaner.name == "gradle":
        value = str(Path(os.environ.get("GRADLE_USER_HOME", home / ".gradle")) / "caches")
    elif cleaner.name == "playwright":
        value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(home / "Library/Caches/ms-playwright"))
    else:
        value = str(home / "Library/Developer/Xcode/DerivedData")
    return validated_path(cleaner.name, value)


def validated_path(name, value):
    home = Path.home()
    path = Path(value).expanduser()
    if not value or "\n" in value or not path.is_absolute():
        raise OSError(f"{name} did not provide an absolute global cache path: {display(value)}")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise OSError(f"refusing symlink cache path: {display(parent)}")
    resolved = path.resolve()
    # Never accept a tool/configuration pointing at a broad directory.
    protected = (home.resolve(),)
    if resolved == Path("/") or any(resolved == parent or resolved in parent.parents for parent in protected):
        raise OSError(f"refusing broad cache path: {display(path)}")
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    if not path.is_dir():
        raise OSError(f"not a cache directory: {display(path)}")
    if name == "playwright":
        foreign = sorted(entry.name for entry in path.iterdir()
                         if entry.name not in PLAYWRIGHT_EXTRAS and not PLAYWRIGHT_BROWSER.fullmatch(entry.name))
        if foreign:
            raise OSError(f"folder holds not only Playwright browsers: {display(foreign[0])}")
    return path


def pnpm_version(tool, prefix=()):
    version = query(tool, (*prefix, "--version"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise OSError(f"unsupported pnpm version: {display(version)}")
    return version


def installed_pnpm():
    """Find Corepack's existing packages, without asking it to fetch a version."""
    packages = corepack_home() / "v1/pnpm"
    if not packages.exists():
        return []
    validated_path("Corepack pnpm packages", str(packages))
    newest = {}
    for package in packages.iterdir():
        if not re.fullmatch(r"\d+\.\d+\.\d+", package.name):
            continue
        version = tuple(int(part) for part in package.name.split("."))
        if version[0] < 10:
            continue
        validated_path("Corepack pnpm package", str(package))
        manifest = package / "package.json"
        if manifest.is_symlink():
            raise OSError(f"refusing symlink pnpm manifest: {display(manifest)}")
        try:
            metadata = json.loads(manifest.read_text())
        except (ValueError, OSError) as exc:
            raise OSError(f"cannot read installed pnpm manifest: {display(manifest)}") from exc
        if (not isinstance(metadata, dict) or metadata.get("name") != "pnpm"
                or metadata.get("version") != package.name):
            raise OSError(f"installed pnpm manifest mismatch: {display(manifest)}")
        if version[0] not in newest or version > newest[version[0]][0]:
            newest[version[0]] = (version, package)
    candidates = []
    for version, package in sorted(newest.values(), reverse=True):
        # Newer Corepack wrappers can download a native binary on first use.
        native = package / "pnpm-native"
        if native.exists():
            if native.is_symlink() or not native.is_file() or not os.access(native, os.X_OK):
                raise OSError(f"invalid installed pnpm binary: {display(native)}")
            candidates.append((str(native), ()))
        elif version[0] in (10, 11):
            entry = package / ("bin/pnpm.cjs" if version[0] == 10 else "bin/pnpm.mjs")
            if any(parent.is_symlink() for parent in (entry, *entry.parents)) or not entry.is_file():
                raise OSError(f"invalid installed pnpm entry point: {display(entry)}")
            node = shutil.which("node")
            if not node:
                raise OSError("node is required to run an installed pnpm")
            candidates.append((str(Path(node).resolve()), (str(entry),)))
    return candidates


def pnpm_cleaners():
    executable = shutil.which("pnpm")
    if not executable:
        raise OSError("pnpm is not installed")
    current = replace(BY_NAME["pnpm"], tool=str(Path(executable).resolve()))
    version = pnpm_version(current.tool)
    current = replace(current, version=version)
    path = cache_path(current)
    if not re.fullmatch(r"v\d+", path.name):
        raise OSError(f"unsupported pnpm store layout: {display(path)}")
    stores = {path}
    if path.parent.exists():
        for sibling in path.parent.iterdir():
            if re.fullmatch(r"v\d+", sibling.name):
                stores.add(validated_path("pnpm store", str(sibling)))
    cleaners = [current]
    uncovered = stores - {path}
    if uncovered:
        for tool, prefix in installed_pnpm():
            version = pnpm_version(tool, prefix)
            options = (*prefix, "--store-dir", str(path.parent))
            candidate = replace(current, tool=tool, prefix=prefix, version=version,
                                query=(*options, "store", "path"),
                                command=(*options, "store", "prune"))
            target = cache_path(candidate)
            if target in uncovered:
                cleaners.append(replace(candidate, name=f"pnpm-{target.name}"))
                uncovered.remove(target)
            if not uncovered:
                break
    if uncovered:
        names = ", ".join(sorted(target.name for target in uncovered))
        raise OSError(f"no installed compatible pnpm for stores: {names}; "
                      "keep them or install a compatible pnpm separately")
    return cleaners


def cleaner_paths(cleaner):
    if cleaner.version and pnpm_version(cleaner.tool, cleaner.prefix) != cleaner.version:
        raise OSError("pnpm version changed after preview")
    paths = [(cleaner.name, cache_path(cleaner))]
    if cleaner.name in ("npm", "yarn"):
        protected = [*fnm_installations(), corepack_home() / "v1",
                     *(Path(value) for value in (cleaner.tool, *cleaner.prefix) if Path(value).is_absolute())]
        for root in (path.resolve() for _, path in paths):
            if any(root == path.resolve() or root in path.resolve().parents for path in protected):
                raise OSError("package cache would include installed tools")
    if cleaner.version:
        major = int(cleaner.version.split(".")[0])
        if major >= 11:
            value = query(cleaner.tool, (*cleaner.prefix, "cache", "path"))
        else:
            value = query(cleaner.tool, (*cleaner.prefix, "config", "get", "cache-dir"))
            if value == "undefined":
                value = str(Path(os.environ.get(
                    "XDG_CACHE_HOME", Path.home() / "Library/Caches")) / "pnpm")
        cache = validated_path("pnpm cache", value)
        store = paths[0][1]
        cache_root, store_root = cache.resolve(), store.resolve()
        if cache_root == store_root or cache_root in store_root.parents or store_root in cache_root.parents:
            raise OSError("pnpm store and metadata cache paths overlap")
        paths.append(("pnpm-cache", cache))
    if cleaner.name == "mise":
        paths.extend(mise_auxiliary_paths(cleaner.tool, paths[0][1]))
    return paths


def mise_auxiliary_paths(tool, cache):
    try:
        settings = json.loads(query(tool, ("settings", "--json")))
    except ValueError as exc:
        raise OSError("mise returned invalid settings JSON") from exc
    if not isinstance(settings, dict) or not isinstance(settings.get("task", {}), dict):
        raise OSError("mise returned invalid task-cache settings")
    task = settings.get("task", {}).get("cache_dir")
    if task is not None and not isinstance(task, str):
        raise OSError("mise returned an invalid task-cache directory")
    state = Path(os.environ.get("MISE_STATE_DIR", Path(os.environ.get(
        "XDG_STATE_HOME", Path.home() / ".local/state")) / "mise"))
    paths = [("mise-env", validated_path("mise environment cache", str(state / "env-cache")))]
    if task:
        paths.append(("mise-tasks", validated_path("mise task cache", task)))
    data = Path(os.environ.get("MISE_DATA_DIR", Path(os.environ.get(
        "XDG_DATA_HOME", Path.home() / ".local/share")) / "mise"))
    config = Path(os.environ.get("MISE_CONFIG_DIR", Path(os.environ.get(
        "XDG_CONFIG_HOME", Path.home() / ".config")) / "mise"))
    for root in [cache, *(path for _, path in paths)]:
        for protected in (data, config, state):
            if root.resolve() == protected.resolve() or root.resolve() in protected.resolve().parents:
                raise OSError(f"mise cache would include tool/config/state storage: {display(root)}")
        for protected in (config, data / "installs", data / "downloads"):
            if root.resolve() == protected.resolve() or protected.resolve() in root.resolve().parents:
                raise OSError(f"mise cache is inside tool/config/download storage: {display(root)}")
    return [(label, path) for label, path in paths if path != cache and cache not in path.parents]


def selected_cleaners(name):
    if name == "pnpm":
        return pnpm_cleaners() if shutil.which("pnpm") else []
    if name == "bun":
        return bun_cleaners()
    if name in ("npm", "yarn"):
        cleaners = package_cleaners(name)
        if name == "yarn":
            selected = []
            for cleaner in cleaners:
                # Force modern Yarn's cache and clean command to the global cache,
                # even if HOME's config disables it. Classic ignores these settings.
                environment = (*cleaner.environment, ("YARN_ENABLE_GLOBAL_CACHE", "true"),
                               ("YARN_IGNORE_PATH", "1"), ("YARN_ENABLE_TELEMETRY", "0"))
                selected.append(replace(cleaner, environment=environment))
            return selected
        return cleaners
    cleaner = BY_NAME[name]
    if cleaner.tool == "go":
        executable = go_executable()
        return [replace(cleaner, tool=executable)] if executable else []
    if cleaner.tool:
        executable = shutil.which(cleaner.tool)
        return [replace(cleaner, tool=str(Path(executable).resolve()))] if executable else []
    return [cleaner]


def configured_locations():
    """Return configured paths, unavailable tools and failed queries, without cleaning."""
    locations, skipped, errors = [], [], []
    for name in BY_NAME:
        try:
            cleaners = selected_cleaners(name)
            if not cleaners:
                reason = "no installed tool discovered; known defaults remain listed"
                if name in orphan_caches.SUPPORTED:
                    reason += "; check recognised artifacts with " + orphan_preview(name)
                skipped.append((name, reason))
            for cleaner in cleaners:
                for label, path in cleaner_paths(cleaner):
                    source = f"configured: {name} via {cleaner.tool or 'environment/default'}"
                    if cleaner.prefix:
                        source += " " + cleaner.prefix[0]
                    source = source.replace(str(Path.home()), "~")
                    locations.append((label, path, source))
        except OSError as exc:
            errors.append((name, display(exc)))
    return locations, skipped, errors


def parser():
    result = argparse.ArgumentParser(
        prog="clean_caches", description=command_help.description("clean_caches"),
        epilog=command_help.help_epilog(
            "clean_caches", "Runs globally from HOME, not in the current project. Project artifacts: wipe_clean.\n"
            "pnpm includes sibling store generations using installed Corepack pnpm versions,\n"
            "plus native metadata and expired execution-cache pruning. Nothing is installed.\n"
            "Cleaners: " + ", ".join(BY_NAME)),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument("--only", metavar="NAMES", help="comma-separated cleaners; required unless --dry-run")
    result.add_argument("--deep", action="store_true", help="allow Gradle, Go modules and Playwright browser cleanup")
    result.add_argument("--orphaned", action="store_true",
                        help="remove recognised conventional Bun/npm/pip/Go-build cache artifacts only when tool unavailable")
    result.add_argument("--sort", choices=("size",), help="show largest caches first; missing caches last")
    result.add_argument("-n", "--dry-run", action="store_true", help="preview without prompting or cleaning")
    return result


def confirm_cleanup():
    library = Path(__file__).resolve().parents[1] / "package-manager/_update-lib.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1" || exit; confirm_apply "Clean these global caches?" false false',
         "clean-caches-confirm", str(library)],
    )
    if result.returncode not in (0, 1):
        raise OSError(f"confirmation failed with status {result.returncode}")
    return result.returncode == 0


def missing_go_cache(cleaner):
    """Inspect only the conventional location; never make it a cleanup target."""
    label = {"go-modules": "Go modules", "go-build": "Go build"}[cleaner.name]
    path = dict(cache_locations())[label]
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode):
            raise OSError(f"Symlink default cache skipped: {display(parent)}")
    if not path.is_dir():
        raise OSError(f"Default cache is not a directory: {display(path)}")
    return path


def orphan_preview(name):
    return f"clean_caches --only {name} --orphaned --dry-run"


def report_only_caches(names, include_inventory, progress):
    labels = {
        "npm": "npm", "pnpm": "pnpm store", "brew": "Homebrew", "bun": "Bun",
        "pip": "pip", "uv": "uv", "mise": "mise cache",
    }
    known = dict(cache_locations())
    candidates = [(name, known[labels[name]],
                   (f"Tool unavailable. Preview orphan cleanup: {orphan_preview(name)}"
                    if name in orphan_caches.SUPPORTED else "Tool unavailable; report only."))
                  for name in names if name in labels]
    if include_inventory:
        candidates.extend((label, known[label], reason) for label, reason in (
            ("Corepack tools", "Installed package-manager versions; kept."),
            ("Cargo registry", "May contain edited source/offline dependencies; report only."),
            ("Cargo Git", "May contain edited source/offline dependencies; report only."),
            ("mise downloads", "Installer downloads; kept."),
        ))
    report, errors = [], []
    for label, path, reason in candidates:
        try:
            path = validated_path(label, str(path))
            if not path.exists():
                continue
            warnings = {}
            size = scan_sizes(path, False, False, [], include_root=True, warnings=warnings,
                              progress=progress)[Path(".")]
            if warnings:
                raise OSError(f"report-only size incomplete: {display(next(iter(warnings)))}")
            report.append((label, path, size, reason))
        except OSError as exc:
            errors.append((label, display(exc)))
    return report, errors


def orphaned_main(args, names):
    plans, failures = [], 0
    progress = ScanProgress(Path("/"), True)
    try:
        for name in names:
            try:
                if selected_cleaners(name) or (name == "pip" and shutil.which("pip")):
                    raise OSError("tool is installed; omit --orphaned to use its native cleaner")
                item = orphan_caches.plan(name, validated_path)
                if item is not None:
                    plans.append(item)
            except OSError as exc:
                failures += 1
                output.row("fail", name, display(exc))
    finally:
        progress.close()
    if args.sort == "size":
        plans.sort(key=lambda item: -item.size)
    output.section("Orphaned cache cleanup plan")
    output.table_header("%-12s  %10s  %s", "Cache", "Size", "Folder")
    for item in plans:
        label = "~/" + str(item.root.relative_to(Path.home()))
        print(f"  {item.name:<12}  {human_size(item.size):>10}  {display(label)}")
        for name in item.preserved:
            output.note("Preserved: " + display(name))
        if not item.entries:
            output.note("No recognised artifacts to remove.")
    output.section("Notes")
    output.note("Explicit direct deletion of recognised artifacts only; cache roots and unknown entries stay.")
    output.note("Go fuzz inputs, npm logs/npx environments and pip self-check data are preserved.")
    output.note("Missing tools do not prove inactivity. Close cache users first; offline data may be lost.")
    if failures:
        output.note("Nothing cleaned: resolve the failed checks first.")
        return 1
    targets = [item for item in plans if item.entries]
    if not plans:
        output.note("No conventional cache directories found.")
    if args.dry_run or not targets or not confirm_cleanup():
        return 0
    # Revalidate every target before the first deletion, including tool discovery.
    for item in targets:
        try:
            if selected_cleaners(item.name) or (item.name == "pip" and shutil.which("pip")):
                raise OSError("tool became available after preview")
            if orphan_caches.plan(item.name, validated_path) != item:
                raise OSError("orphan cache changed after preview")
        except OSError as exc:
            output.row("fail", item.name, display(exc))
            output.note("Nothing cleaned: orphan checks changed after preview.")
            return 1
    output.section("Cleaning")
    for item in targets:
        try:
            before = shutil.disk_usage(item.root).free
            orphan_caches.remove(item)
            change = shutil.disk_usage(item.root).free - before
            output.row("ok", item.name, "Recognised orphaned artifacts removed")
            output.note(f"Observed available-space change: {'+' if change >= 0 else '-'}{human_size(abs(change))}")
        except OSError as exc:
            failures += 1
            output.row("fail", item.name, display(exc))
    return 1 if failures else 0


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    names = list(dict.fromkeys(args.only.split(","))) if args.only is not None else [
        cleaner.name for cleaner in CLEANERS if args.deep or not cleaner.deep]
    if any(name not in BY_NAME for name in names):
        cli.error("--only must contain cleaner names from: " + ", ".join(BY_NAME))
    if args.orphaned and (args.only is None or any(name not in orphan_caches.SUPPORTED for name in names)):
        cli.error("--orphaned requires explicit --only from: " + ", ".join(orphan_caches.SUPPORTED))
    if not args.dry_run and args.only is None:
        cli.error("explicit --only selection is required unless --dry-run is given")
    if any(BY_NAME[name].deep for name in names) and not args.deep:
        cli.error("Gradle, Go modules and Playwright require --deep")
    if args.dry_run:
        output.dry_run()
    output.context("Global caches")
    if args.orphaned:
        return orphaned_main(args, names)
    progress = ScanProgress(Path("/"), True)
    rows = []
    report = []
    skipped = []
    failures = 0
    blocked = set()
    go = None
    go_error = None
    buns = []
    bun_error = None
    if "bun" in names:
        try:
            buns = bun_cleaners()
        except OSError as exc:
            bun_error = exc
    if any(BY_NAME[name].tool == "go" for name in names):
        try:
            go = go_executable()
        except OSError as exc:
            go_error = exc
    try:
        for name in names:
            cleaner = BY_NAME[name]
            if name == "mise" and shutil.which(cleaner.tool):
                cleaner = replace(cleaner, tool=str(Path(shutil.which(cleaner.tool)).resolve()))
            if name == "bun" and bun_error is not None:
                failures += 1
                report.append((name, None, None, display(bun_error)))
                continue
            if name == "bun" and not buns:
                detail = "Bun unavailable on PATH, in BUN_INSTALL, fnm or mise"
                if args.only:
                    failures += 1
                    report.append((name, None, None, detail))
                skipped.append((name, detail + "; native cleanup unavailable"))
                continue
            if cleaner.tool == "go" and go_error is not None:
                failures += 1
                report.append((name, None, None, display(go_error)))
                continue
            if cleaner.tool == "go" and go:
                cleaner = replace(cleaner, tool=go)
            missing_tool = cleaner.tool and not shutil.which(cleaner.tool) and not (name == "bun" and buns)
            if not args.only and missing_tool and cleaner.tool != "go" and name not in ("npm", "yarn"):
                skipped.append((name, f"{cleaner.tool} unavailable on PATH; configured cache not queried"))
                continue
            try:
                if missing_tool and cleaner.tool == "go":
                    path = missing_go_cache(cleaner)
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        if args.only:
                            report.append((name, path, None, "No cache directory (default location; Go unavailable)"))
                        else:
                            skipped.append((name, "Go unavailable on PATH or in mise; no default cache directory"))
                        continue
                    warnings = {}
                    size = scan_sizes(path, False, False, [], include_root=True, warnings=warnings,
                                      progress=progress)[Path(".")]
                    if warnings:
                        raise OSError(f"default cache size incomplete: {display(next(iter(warnings)))}")
                    failures += 1
                    blocked.add(name)
                    report.append((name, path, size,
                                   "Unverified default; Go unavailable on PATH or in mise; cleanup blocked."))
                    continue
                if name in ("npm", "yarn"):
                    cleaners = selected_cleaners(name)
                    if not cleaners:
                        reason = f"{name} unavailable on PATH or in installed fnm/Corepack versions"
                        if args.only:
                            raise OSError(reason)
                        skipped.append((name, reason + "; configured cache not queried"))
                        continue
                else:
                    cleaners = pnpm_cleaners() if name == "pnpm" else buns if name == "bun" else [cleaner]
                reported = set()
                for selected in cleaners:
                    paths = cleaner_paths(selected)
                    existing = []
                    for label, path in paths:
                        if path in reported:
                            continue
                        reported.add(path)
                        try:
                            path.lstat()
                        except FileNotFoundError:
                            if label != "pnpm-cache":
                                report.append((label, path, None, "No cache directory"))
                            continue
                        existing.append(path)
                        warnings = {}
                        size = scan_sizes(path, False, False, [], include_root=True, warnings=warnings,
                                          progress=progress)[Path(".")]
                        if warnings:
                            raise OSError(f"cache size incomplete: {display(next(iter(warnings)))}")
                        report.append((label, path, size, selected.cost))
                    if existing:
                        rows.append((selected, paths, existing[0]))
            except OSError as exc:
                failures += 1
                report.append((name, None, None, display(exc)))
        inventory, inventory_errors = report_only_caches(
            [name for name, _ in skipped], args.only is None, progress)
        failures += len(inventory_errors)
    finally:
        progress.close()
    if args.sort == "size":
        report.sort(key=lambda item: (item[2] is None, -(item[2] or 0)))
        inventory.sort(key=lambda item: -item[2])
    output.section("Cache cleanup plan")
    output.table_header("%-12s  %10s  %s", "Cache", "Size", "Folder")
    for name, path, size, detail in report:
        if size is None and path is None:
            output.row("fail", name, detail)
        else:
            home = Path.home()
            label = "~/" + str(path.relative_to(home)) if path.is_relative_to(home) else str(path)
            print(f"  {name:<12}  {human_size(size) if size is not None else '—':>10}  "
                  f"{display(label) if size is not None else detail}")
            if name == "yarn" and size is None:
                output.note("Cache location: " + display(label))
            if name in blocked:
                output.note(output.mark("warn") + " " + detail)
                if name in orphan_caches.SUPPORTED:
                    output.note("Check recognised artifacts: " + orphan_preview(name))
    if inventory or inventory_errors:
        output.section("Report-only storage")
        output.table_header("%-18s  %10s  %s", "Cache", "Size", "Folder")
        for name, path, size, reason in inventory:
            label = "~/" + str(path.relative_to(Path.home())) if path.is_relative_to(Path.home()) else str(path)
            print(f"  {name:<18}  {human_size(size):>10}  {display(label)}")
            output.note(reason)
        for name, reason in inventory_errors:
            output.row("fail", name, reason)
    shown = {name for name, _, _, _ in inventory}
    skipped = [(name, reason) for name, reason in skipped if args.only and name not in shown]
    if skipped:
        output.section("Skipped cleaners")
        for name, reason in skipped:
            output.row("warn", name, reason)
    if rows or inventory:
        output.section("Notes")
        output.note("Sizes show disk usage, not how much cleanup would free.")
        if inventory:
            output.note("Report-only storage is excluded from this cleanup and confirmation.")
            if any(name in orphan_caches.SUPPORTED for name, _, _, _ in inventory):
                output.note("Orphan previews verify recognised artifacts separately; unknown entries stay.")
        if any(cleaner.version for cleaner, _, _ in rows):
            output.note("pnpm: native pruning of each listed store, metadata and expired dlx/pnx caches.")
            output.note("Unexpired execution caches and installed tool versions are not blanket-deleted.")
            output.note("Custom project stores outside the listed store root are not discovered.")
        if any(cleaner.name == "mise" for cleaner, _, _ in rows):
            output.note("mise: native age/TTL pruning includes task and environment caches; installed tools are kept.")
    if failures:
        if not args.dry_run:
            output.note("Nothing cleaned: resolve the failed checks first.")
        return 1
    if args.dry_run or not rows:
        return 0
    if not confirm_cleanup():
        return 0
    output.section("Cleaning")
    for cleaner, paths, path in rows:
        try:
            if cleaner_paths(cleaner) != paths:
                raise OSError("configured cache location changed after preview")
            before = shutil.disk_usage(path.parent).free
            if cleaner.command:
                result = subprocess.run([cleaner.tool, *cleaner.command], cwd=Path.home(),
                                        env=tool_environment(cleaner.environment))
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
