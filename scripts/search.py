#!/usr/bin/env python3
"""Focused ripgrep search with concise snippets for long matching lines."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
CLEAN_TARGETS = SCRIPT_DIR / "lib" / "clean-targets.sh"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
import command_help  # noqa: E402
import output as terminal_output  # noqa: E402


def nonnegative(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


class SearchParser(argparse.ArgumentParser):
    def format_usage(self):
        return super().format_usage().replace("usage:", "Usage:", 1)

    def format_help(self):
        return super().format_help().replace("usage:", "Usage:", 1)

    def error(self, message):
        super().error(f"{message}. See search --help for supported options.")


class ContextAction(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        options = list(getattr(namespace, self.dest, None) or ())
        options.append((option_string, value))
        setattr(namespace, self.dest, options)


def make_parser():
    parser = SearchParser(
        prog="search",
        usage="search [OPTIONS] PATTERN [PATH...]",
        description=command_help.description("search"),
        epilog=command_help.help_epilog("search"),
    )
    parser.add_argument("-i", "--ignore-case", action="store_true", help="Match case-insensitively")
    parser.add_argument("-S", "--smart-case", action="store_true", help="Use smart case matching")
    parser.add_argument("-w", "--word-regexp", action="store_true", help="Match whole words")
    parser.add_argument("-F", "--fixed-strings", action="store_true", help="Treat the pattern literally")
    parser.add_argument("-g", "--glob", action="append", default=[], metavar="GLOB",
                        help="Include or exclude paths (repeatable)")
    parser.add_argument("-t", "--type", action="append", default=[], metavar="TYPE",
                        help="Search files of this type (repeatable)")
    parser.add_argument("-T", "--type-not", action="append", default=[], metavar="TYPE",
                        help="Exclude files of this type (repeatable)")
    parser.add_argument("-A", "--after-context", dest="context_options", action=ContextAction,
                        type=nonnegative, metavar="NUM",
                        help="Show NUM following lines")
    parser.add_argument("-B", "--before-context", dest="context_options", action=ContextAction,
                        type=nonnegative, metavar="NUM",
                        help="Show NUM preceding lines")
    parser.add_argument("-C", "--context", dest="context_options", action=ContextAction,
                        type=nonnegative, metavar="NUM",
                        help="Show NUM lines before and after matches")
    parser.add_argument("--hidden", action="store_true", help="Search hidden files and directories")
    parser.add_argument("-L", "--follow", action="store_true", help="Follow symbolic links")
    parser.add_argument("-M", "--max-columns", dest="snippet_width", type=nonnegative,
                        default=200, metavar="WIDTH",
                        help="Snippet width for long lines; 0 shows full lines (default: 200)")
    parser.add_argument("-n", "--line-number", action="store_true", help="Show line numbers (default)")
    parser.add_argument("--no-trim", action="store_true",
                        help="Preserve leading spaces and tabs on matching lines")
    parser.add_argument("--no-clean", action="store_true",
                        help="Include shared clean targets; .git remains excluded")
    parser.add_argument("pattern", help="Search pattern")
    parser.add_argument("paths", nargs="*", help="Files or directories to search")
    return parser


def clean_targets():
    command = (
        'source "$1" || exit; '
        'printf "%s\\0" "${CLEAN_DIR_TARGETS[@]}" "${CLEAN_FILE_TARGETS[@]}"'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command, "search-clean-targets", str(CLEAN_TARGETS)],
        check=False, capture_output=True,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "could not load shared clean-target exclusions")
    return [value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def rg_arguments(args):
    command = ["rg", "--json", "--line-number", "--color", "never"]
    for enabled, option in (
        (args.ignore_case, "--ignore-case"),
        (args.smart_case, "--smart-case"),
        (args.word_regexp, "--word-regexp"),
        (args.fixed_strings, "--fixed-strings"),
        (args.hidden, "--hidden"),
        (args.follow, "--follow"),
    ):
        if enabled:
            command.append(option)
    for option, values in (
        ("--glob", args.glob),
        ("--type", args.type),
        ("--type-not", args.type_not),
    ):
        for value in values:
            command.extend((option, value))
    for option, value in args.context_options or ():
        command.extend((option, str(value)))
    command.extend(("--glob", "!.git"))
    if not args.no_clean:
        for target in clean_targets():
            command.extend(("--glob", f"!{target}"))
    command.append("--")
    command.append(args.pattern)
    paths = args.paths
    if not paths and sys.stdin.isatty():
        paths = ["."]
    command.extend(paths)
    return command


def decode_text(value):
    if "text" in value:
        return value["text"]
    return base64.b64decode(value["bytes"]).decode("utf-8", "surrogateescape")


def matching_line(event, trim):
    data = event["data"]
    raw = data["lines"].get("text")
    if raw is not None:
        raw = raw.encode("utf-8", "surrogateescape")
    else:
        raw = base64.b64decode(data["lines"]["bytes"])
    text = raw.decode("utf-8", "surrogateescape")
    if text.endswith("\n"):
        text = text[:-1]
    if text.endswith("\r"):
        text = text[:-1]
    spans = []
    for match in data.get("submatches", []):
        start = len(raw[:match["start"]].decode("utf-8", "surrogateescape"))
        end = len(raw[:match["end"]].decode("utf-8", "surrogateescape"))
        spans.append((start, end))
    if trim:
        leading = len(text) - len(text.lstrip(" \t"))
        first_match = min((start for start, _ in spans), default=leading)
        removed = min(leading, first_match)
        text = text[removed:]
        spans = [(max(0, start - removed), max(0, end - removed)) for start, end in spans]
        spans = [(start, end) for start, end in spans if end > start]
    return text, spans


def render_match(text, spans, left, right):
    pieces = []
    position = left
    for start, end in spans:
        if end <= left or start >= right:
            continue
        start, end = max(left, start), min(right, end)
        start = max(position, start)
        if end <= start:
            continue
        pieces.append(escape_controls(text[position:start]))
        pieces.append(terminal_output.paint(escape_controls(text[start:end]), "red"))
        position = end
    pieces.append(escape_controls(text[position:right]))
    return "".join(pieces)


def snippet(text, spans, width):
    if width == 0 or len(text) <= width or not spans:
        return [render_match(text, spans, 0, len(text))]

    spans.sort()
    groups = []
    group_start, group_end = spans[0]
    for start, end in spans[1:]:
        if end - group_start <= width:
            group_end = max(group_end, end)
        else:
            groups.append((group_start, group_end))
            group_start, group_end = start, end
    groups.append((group_start, group_end))

    rendered = []
    for start, end in groups:
        span_width = end - start
        window_width = max(width, span_width)
        left = max(0, start - (window_width - span_width) // 2)
        right = min(len(text), left + window_width)
        left = max(0, right - window_width)
        prefix = "... " if left else ""
        suffix = " ..." if right < len(text) else ""
        rendered.append(prefix + render_match(text, spans, left, right) + suffix)
    return rendered


def write_record(event, args, headings, heading_state):
    data = event["data"]
    path = decode_text(data["path"])
    line_number = data.get("line_number")
    if event["type"] == "match":
        text, spans = matching_line(event, not args.no_trim)
        lines = snippet(text, spans, args.snippet_width)
        separator = ":"
    else:
        lines = [escape_controls(decode_text(data["lines"]).removesuffix("\n").removesuffix("\r"))]
        separator = "-"

    output = []
    is_stdin = path in ("-", "<stdin>")
    if headings and not is_stdin:
        if heading_state[0] != path:
            if heading_state[0] is not None:
                output.append("\n")
            output.append(terminal_output.paint(escape_controls(path), "cyan") + "\n")
            heading_state[0] = path
        prefix = terminal_output.paint(str(line_number), "green") + separator
    else:
        prefix = (f"{escape_controls(path)}:{line_number}{separator}" if not is_stdin
                  else f"{line_number}{separator}")
    for line in lines:
        output.append(prefix + line + "\n")
    sys.stdout.buffer.write("".join(output).encode("utf-8", "surrogateescape"))


def escape_controls(text):
    return "".join(
        f"\\x{ord(char):02x}" if (ord(char) < 32 and char != "\t") or 127 <= ord(char) <= 159
        else char for char in text
    )



def run(args):
    try:
        command = rg_arguments(args)
        process = subprocess.Popen(command, stdout=subprocess.PIPE)
    except (OSError, RuntimeError) as error:
        print(f"search: {error}", file=sys.stderr)
        return 127 if isinstance(error, FileNotFoundError) else 2

    heading_state = [None]
    headings = sys.stdout.isatty()
    try:
        for raw_event in process.stdout:
            event = json.loads(raw_event)
            if event["type"] in ("match", "context"):
                write_record(event, args, headings, heading_state)
        return process.wait()
    except BrokenPipeError:
        process.terminate()
        process.wait()
        sys.stdout = open(os.devnull, "w")
        return 0
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        process.terminate()
        process.wait()
        print(f"search: could not read ripgrep JSON output: {error}", file=sys.stderr)
        return 2


def parse_arguments(parser, argv):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--" not in argv:
        return parser.parse_intermixed_args(protect_no_clean_values(argv))

    separator = argv.index("--")
    before, after = argv[:separator], argv[separator + 1:]
    placeholder = "\0search-pattern-placeholder"
    args = parser.parse_intermixed_args(protect_no_clean_values(before) + [placeholder])
    if args.pattern == placeholder:
        if not after:
            parser.error("the following arguments are required: pattern")
        args.pattern, *args.paths = after
    else:
        if args.paths and args.paths[-1] == placeholder:
            args.paths.pop()
        args.paths.extend(after)
    return args


def protect_no_clean_values(argv):
    value_options = {
        "-g": "--glob", "--glob": "--glob",
        "-t": "--type", "--type": "--type",
        "-T": "--type-not", "--type-not": "--type-not",
        "-M": "--max-columns", "--max-columns": "--max-columns",
        "-A": "--after-context", "--after-context": "--after-context",
        "-B": "--before-context", "--before-context": "--before-context",
        "-C": "--context", "--context": "--context",
    }
    protected = []
    index = 0
    while index < len(argv):
        option = value_options.get(argv[index])
        if option and index + 1 < len(argv) and argv[index + 1] == "--no-clean":
            protected.append(f"{option}=--no-clean")
            index += 2
        else:
            protected.append(argv[index])
            index += 1
    return protected


def main(argv=None):
    parser = make_parser()
    args = parse_arguments(parser, argv)
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
