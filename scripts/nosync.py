#!/usr/bin/env python3
"""Mark folders to keep out of iCloud sync, or inspect their ignore attribute."""

import argparse
import ctypes
import errno
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
import output  # noqa: E402


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


def find_folders(names):
    folders = []
    pending = ["."]
    while pending:
        folder = pending.pop()
        if (
            (folder != "." and os.path.basename(folder) in names)
            or read_ignore_attribute(folder) == b"1"
        ):
            folders.append(folder)
            continue
        with os.scandir(folder) as entries:
            children = [entry.path for entry in entries if entry.is_dir(follow_symlinks=False)]
        pending.extend(reversed(children))
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
            "Mark folders to keep out of iCloud sync. Defaults to node_modules and .git; "
            "--inspect alone defaults to the current folder. Recursive searches use exact "
            "folder names and stop at matches or folders whose ignore attribute is exactly 1. "
            "MARKED reports the attribute, not verified iCloud behavior. "
            "Set NO_COLOR=1 to disable terminal colors."
        ),
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
    parser.add_argument("folders", nargs="*", help="folder paths, or exact names in recursive mode")
    args = parser.parse_args(argv)
    if args.sort == "status" and not (args.recursive and args.inspect):
        parser.error("--sort status requires --recursive and --inspect")
    folders = args.folders or (["."] if args.inspect and not args.recursive else ["node_modules", ".git"])

    if args.recursive:
        for folder in folders:
            if not folder or "/" in folder or folder in (".", ".."):
                parser.error(f"Recursive mode requires folder names, not paths: {folder}")
        try:
            # Finish discovery before writing any attributes, so search errors cannot partially apply.
            folders = find_folders(set(folders))
        except OSError as exc:
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
