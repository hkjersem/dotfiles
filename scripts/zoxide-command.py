#!/usr/bin/env python3
"""Use a zoxide directory match without changing the current directory."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import command_help


COMMANDS = {
    "--list": ("zla", ["ls", "-lAh", "--"]),
    "--code": ("zcode", ["code", "--"]),
    "--open": ("zopen", ["open", "--"]),
    "--fork": ("zfork", ["open", "-a", "Fork", "--"]),
}


class Parser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace("usage:", "Usage:", 1)

    def error(self, message):
        self.exit(2, f"Error: {message}\n")


def main():
    arguments = sys.argv[1:]
    mode = arguments.pop(0) if arguments and arguments[0] in COMMANDS else "--list"
    name, command = COMMANDS[mode]
    parser = Parser(
        prog=name,
        usage=f"{name} [NAME...]",
        description=command_help.description(name),
        epilog=command_help.help_epilog(name) + "\nWith no name, choose a directory with fzf.",
    )
    parser.add_argument("names", nargs="*", metavar="NAME", help="Directory-matching keywords")
    args = parser.parse_args(arguments)
    query = ["zoxide", "query"]
    if not args.names:
        query.append("--interactive")
    query.extend(["--", *args.names])
    try:
        result = subprocess.run(query, stdout=subprocess.PIPE, text=True, check=False)
        if result.returncode:
            return result.returncode
        directory = result.stdout.removesuffix("\n")
        if not os.path.isabs(directory) or not os.path.isdir(directory):
            print("Error: zoxide did not return an existing absolute directory", file=sys.stderr)
            return 1
        return subprocess.run([*command, directory], check=False).returncode
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
