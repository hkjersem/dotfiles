#!/usr/bin/env python3
"""Mark folders to keep out of iCloud sync, or inspect their ignore attribute."""

import argparse
import ctypes
import errno
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
import output  # noqa: E402
import command_help  # noqa: E402


ATTRIBUTE = "com.apple.fileprovider.ignore#P"
_libc = ctypes.CDLL(None, use_errno=True)
_libc.getxattr.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_uint32, ctypes.c_int,
]
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.setxattr.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_uint32, ctypes.c_int,
]
_libc.setxattr.restype = ctypes.c_int


def get_attribute(folder, attribute):
    # macOS has no os.getxattr; call its native API without a child process.
    args = os.fsencode(folder), attribute.encode()
    size = _libc.getxattr(*args, None, 0, 0, 0)
    if size < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), folder)
    buffer = ctypes.create_string_buffer(size)
    size = _libc.getxattr(*args, buffer, size, 0, 0)
    if size < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), folder)
    return buffer.raw[:size]


def set_attribute(folder, attribute, value):
    if _libc.setxattr(os.fsencode(folder), attribute.encode(), value, len(value), 0, 0) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), folder)


def read_ignore_attribute(folder):
    try:
        return get_attribute(folder, ATTRIBUTE)
    except OSError as exc:
        if exc.errno in (getattr(errno, "ENOATTR", None), getattr(errno, "ENODATA", None)):
            return None
        raise


def is_excluded(relative, excludes):
    return any(relative == excluded or relative.startswith(excluded + "/") for excluded in excludes)


def has_excluded_descendant(relative, excludes):
    prefix = "" if relative == "." else relative + "/"
    return any(excluded.startswith(prefix) for excluded in excludes)


def find_folders(names, *, skip_git=False, excludes=()):
    folders = []
    pending = ["."]
    while pending:
        folder = pending.pop()
        relative = folder[2:] if folder.startswith("./") else folder
        if folder != "." and is_excluded(relative, excludes):
            continue

        name_match = folder != "." and os.path.basename(folder) in names
        marked = read_ignore_attribute(folder) == b"1"
        contains_exclusion = has_excluded_descendant(relative, excludes)
        if marked and contains_exclusion:
            raise RuntimeError(
                f"cannot honor --exclude inside already marked folder: {folder}"
            )
        if (name_match or marked) and not contains_exclusion:
            folders.append(folder)
            continue
        with os.scandir(folder) as entries:
            children = [
                entry.path for entry in entries
                if entry.is_dir(follow_symlinks=False)
                and not (skip_git and entry.name == ".git")
            ]
        pending.extend(reversed(children))
    return folders


def clean_dir_targets():
    library = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib", "clean-targets.sh")
    result = subprocess.run(
        ["/bin/bash", "-c",
         'source "$1" || exit\nprintf "%s\\0" "${CLEAN_DIR_TARGETS[@]}"',
         "nosync-clean-targets", library],
        capture_output=True,
    )
    if result.returncode:
        detail = os.fsdecode(result.stderr).strip()
        raise RuntimeError(detail or "could not load shared clean targets")
    return [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def find_gitignore_folders(ignore_file, excludes):
    with open(ignore_file, "rb") as source:
        ignore_contents = source.read()

    folders = []
    if read_ignore_attribute(".") == b"1":
        if excludes:
            raise RuntimeError("cannot honor --exclude inside already marked folder: .")
        return ["."]
    pending = ["."]
    with tempfile.TemporaryDirectory() as temp:
        with open(os.path.join(temp, ".gitignore"), "wb") as target:
            target.write(ignore_contents)

        git_env = os.environ.copy()
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
            git_env.pop(key, None)
        for key in list(git_env):
            if key.startswith("GIT_CONFIG_"):
                git_env.pop(key)
        git_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})

        initialized = subprocess.run(
            ["git", "-C", temp, "init", "-q"],
            env=git_env, capture_output=True, check=False,
        )
        if initialized.returncode:
            detail = initialized.stderr.decode(errors="replace").strip()
            raise RuntimeError(detail or "git init failed")

        while pending:
            folder = pending.pop()
            with os.scandir(folder) as entries:
                children = [
                    entry.path for entry in entries
                    if entry.name != ".git" and entry.is_dir(follow_symlinks=False)
                ]
            searchable = []
            for child in children:
                relative = child[2:] if child.startswith("./") else child
                if is_excluded(relative, excludes):
                    continue
                marked = read_ignore_attribute(child) == b"1"
                if marked and has_excluded_descendant(relative, excludes):
                    raise RuntimeError(
                        f"cannot honor --exclude inside already marked folder: {child}"
                    )
                if marked:
                    folders.append(child)
                else:
                    searchable.append((child, relative))

            if searchable:
                paths = [path + "/" for _, path in searchable]
                matched = subprocess.run(
                    [
                        "git", "-C", temp, "-c", f"core.excludesFile={os.devnull}",
                        "check-ignore", "--no-index", "-z", "--stdin",
                    ],
                    input=b"\0".join(os.fsencode(path) for path in paths) + b"\0",
                    env=git_env, capture_output=True, check=False,
                )
                if matched.returncode not in (0, 1):
                    detail = matched.stderr.decode(errors="replace").strip()
                    raise RuntimeError(detail or "git check-ignore failed")
                ignored = {
                    os.fsdecode(path) for path in matched.stdout.split(b"\0") if path
                }
                for (child, relative), path in zip(searchable, paths):
                    if path in ignored and not has_excluded_descendant(relative, excludes):
                        folders.append(child)
                    else:
                        pending.append(child)
    return folders


def report(label, folder, detail="", *, error=False):
    stream = sys.stderr if error else sys.stdout
    kind = {
        "MARKED": "ok", "SET": "ok", "ALREADY": "ok",
        "NOT SET": "warn", "VALUE": "warn", "ERROR": "fail",
    }[label]
    color, symbol = output.MARKS[kind]
    status = output.paint(f"{symbol} {label:<9}", color, stream)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status} {folder}{suffix}", file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="nosync",
        allow_abbrev=False,
        description=(
            command_help.description("nosync") + "\n\nDefaults to node_modules and .git; "
            "--inspect alone defaults to the current folder. Recursive searches use exact "
            "folder names, or Git ignore rules from --gitignore, and stop at matches or folders "
            "whose ignore attribute is exactly 1. --exclude with --gitignore or --clean-targets "
            "omits exact relative paths and their contents, including by searching inside a "
            "matched parent when needed. "
            "MARKED reports the attribute, not verified iCloud behavior. "
            "Set NO_COLOR=1 to disable terminal colors."
        ),
        epilog=command_help.examples("nosync"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--inspect", action="store_true", help="report the attribute without changes")
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="find matching folders below here without following symlinks or creating folders",
    )
    parser.add_argument(
        "--sort", choices=("path", "status"), default="path",
        help="with --recursive --inspect: path (default), or folders that sync first",
    )
    parser.add_argument(
        "--gitignore", metavar="FILE",
        nargs="?", const=".gitignore",
        help="find folders ignored by FILE (default: .gitignore; implies --recursive)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATH",
        help="with --gitignore or --clean-targets: exclude this relative path and its contents (repeatable)",
    )
    parser.add_argument(
        "--clean-targets", action="store_true",
        help="find shared generated-directory targets below here (implies --recursive)",
    )
    parser.add_argument("folders", nargs="*", help="folder paths, or exact names in recursive mode")
    args = parser.parse_args(argv)
    recursive = args.recursive or args.gitignore is not None or args.clean_targets
    if args.gitignore is not None and args.folders:
        parser.error("folder names cannot be combined with --gitignore")
    if args.exclude and args.gitignore is None and not args.clean_targets:
        parser.error("--exclude requires --gitignore or --clean-targets")
    if args.gitignore is not None and args.clean_targets:
        parser.error("--gitignore and --clean-targets cannot be combined")
    if args.sort == "status" and not (recursive and args.inspect):
        parser.error("--sort status requires --recursive and --inspect")
    excludes = []
    for path in args.exclude:
        normalized = os.path.normpath(path)
        if (
            os.path.isabs(path) or normalized in (".", "..")
            or normalized.startswith(".." + os.sep)
        ):
            parser.error(f"--exclude requires a relative folder path below the current directory: {path}")
        excludes.append(normalized)
    names = list(args.folders)
    if args.clean_targets:
        try:
            names.extend(clean_dir_targets())
        except (OSError, RuntimeError) as exc:
            print(f"Could not load shared clean targets: {exc}", file=sys.stderr)
            return 1
    folders = names or (["."] if args.inspect and not recursive else ["node_modules", ".git"])

    if recursive:
        if args.gitignore is not None:
            try:
                folders = find_gitignore_folders(args.gitignore, excludes)
            except (OSError, RuntimeError) as exc:
                print(f"Could not complete recursive folder search: {exc}", file=sys.stderr)
                return 1
        else:
            for folder in folders:
                if not folder or "/" in folder or folder in (".", ".."):
                    parser.error(f"Recursive mode requires folder names, not paths: {folder}")
            try:
                # Finish discovery before writing any attributes, so search errors cannot partially apply.
                if args.clean_targets:
                    folders = find_folders(
                        set(folders), skip_git=True, excludes=excludes,
                    )
                else:
                    folders = find_folders(set(folders))
            except (OSError, RuntimeError) as exc:
                print(f"Could not complete recursive folder search: {exc}", file=sys.stderr)
                return 1
        if not folders:
            output.note("No matching folders found below the current directory")
            return 0
        folders.sort()
        if args.sort == "status":
            # Stable sort keeps path order within each group; unreadable folders stay with those that sync.
            def marked(folder):
                try:
                    return read_ignore_attribute(folder) == b"1"
                except OSError:
                    return False
            folders.sort(key=marked)

    status = 0
    for folder in folders:
        try:
            if not args.inspect and not args.recursive:
                os.makedirs(folder, exist_ok=True)
            value = read_ignore_attribute(folder)
            if args.inspect:
                if value is None:
                    report("NOT SET", folder)
                elif value == b"1":
                    report("MARKED", folder)
                else:
                    report("VALUE", folder, f"attribute={value!r}")
            elif value == b"1":
                report("ALREADY", folder)
            else:
                set_attribute(folder, ATTRIBUTE, b"1")
                report("SET", folder)
        except OSError as exc:
            report("ERROR", folder, str(exc), error=True)
            status = 1
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
