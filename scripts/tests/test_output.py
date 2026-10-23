import io
import os
import pty
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import output  # noqa: E402

COLORS = ("bold", "dim", "red", "green", "yellow", "cyan")
MARKS = ("ok", "warn", "fail", "info")

BASH = f"""
source {str(LIB / "output.sh")!r}
out_section "Name"
out_subsection "Sub"
for mark in "$OUT_OK" "$OUT_WARN" "$OUT_FAIL" "$OUT_INFO"; do printf '  %s text\\n' "$mark"; done
for color in "$BOLD" "$DIM" "$RED" "$GREEN" "$YELLOW" "$CYAN"; do printf '%s\\n' "${{color}}x${{RESET}}"; done
"""


def read_terminal(main):
    chunks = []
    while True:
        try:
            chunk = os.read(main, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(main)
    # The terminal turns each newline into a carriage return and a newline.
    return b"".join(chunks).decode().replace("\r\n", "\n")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def bash_output(terminal, no_color):
    env = {**os.environ, "NO_COLOR": no_color}
    if not terminal:
        return subprocess.run(["/bin/bash", "-c", BASH], env=env, capture_output=True, text=True,
                              check=True).stdout
    main, follower = pty.openpty()
    process = subprocess.Popen(["/bin/bash", "-c", BASH], env=env, stdout=follower)
    os.close(follower)
    text = read_terminal(main)
    if process.wait() != 0:
        raise AssertionError(f"bash exited with {process.returncode}")
    return text


STDERR_BASH = f"""
source {str(LIB / "output.sh")!r}
printf '%s %s\\n' "$ERR_WARN" "$ERR_FAIL" >&2
"""


def bash_stderr(stderr_is_terminal):
    """Run STDERR_BASH with exactly one of stdout and stderr on a terminal; return what reached stderr."""
    env = {**os.environ, "NO_COLOR": ""}
    main, follower = pty.openpty()
    if stderr_is_terminal:
        process = subprocess.Popen(["/bin/bash", "-c", STDERR_BASH], env=env,
                                   stdout=subprocess.DEVNULL, stderr=follower)
    else:
        process = subprocess.Popen(["/bin/bash", "-c", STDERR_BASH], env=env,
                                   stdout=follower, stderr=subprocess.PIPE)
    os.close(follower)
    on_terminal = read_terminal(main)
    piped = process.stderr.read().decode() if process.stderr else ""
    if process.wait() != 0:
        raise AssertionError(f"bash exited with {process.returncode}")
    return on_terminal if stderr_is_terminal else piped


def python_output(terminal, no_color):
    stream = Terminal() if terminal else io.StringIO()
    with patch.dict(os.environ, {"NO_COLOR": no_color}), redirect_stdout(stream):
        output.section("Name")
        output.subsection("Sub")
        for kind in MARKS:
            print(f"  {output.mark(kind)} text")
        for color in COLORS:
            print(output.paint("x", color))
    return stream.getvalue()


class OutputTests(unittest.TestCase):
    def test_bash_and_python_print_the_same_bytes(self):
        for terminal, no_color in ((True, ""), (True, "1"), (False, "")):
            with self.subTest(terminal=terminal, no_color=no_color):
                self.assertEqual(python_output(terminal, no_color), bash_output(terminal, no_color))

    def test_plain_output_carries_meaning_without_escape_codes(self):
        self.assertEqual(bash_output(False, ""), "\n".join([
            "",
            "── Name ──────────────────────────────────",
            "",
            "  ▸ Sub",
            "  ✓ text", "  ! text", "  ✗ text", "  • text",
            *["x"] * len(COLORS),
        ]) + "\n")

    def test_terminal_output_is_colored(self):
        colored = bash_output(True, "")
        self.assertIn("\033[1m── Name ", colored)
        self.assertIn("  \033[0;32m✓\033[0m text", colored)
        self.assertIn("  \033[0;31m✗\033[0m text", colored)

    def test_stderr_marks_follow_whether_stderr_is_a_terminal(self):
        self.assertEqual(bash_stderr(False), "! ✗\n")
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            expected = f"{output.mark('warn', Terminal())} {output.mark('fail', Terminal())}\n"
        self.assertEqual(bash_stderr(True), expected)
        self.assertIn("\033[", expected)


if __name__ == "__main__":
    unittest.main()
