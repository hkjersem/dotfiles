import io
import os
import pty
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import output  # noqa: E402

COLORS = ("bold", "dim", "red", "green", "yellow", "cyan")
MARKS = ("ok", "warn", "fail", "info")

BASH = f"""
source {str(LIB / "output.sh")!r}
out_title "Name"
out_section "Name"
out_section "Åxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
out_subsection "Sub"
for mark in "$OUT_OK" "$OUT_WARN" "$OUT_FAIL" "$OUT_INFO"; do printf '  %s text\\n' "$mark"; done
for color in "$BOLD" "$DIM" "$RED" "$GREEN" "$YELLOW" "$CYAN"; do printf '%s\\n' "${{color}}x${{RESET}}"; done
out_dry_run "Run x to apply."
out_dry_run
out_context "a" "b c"
out_note "note"
out_row "$OUT_WARN" "Label" "detail"
out_table_header "%-6s  %s" "A" "B"
out_summary 0 0 "All good"
out_summary 2 1 "All good"
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
    # A byte-counting locale must not change the width of headings with non-ASCII titles.
    env = {**os.environ, "NO_COLOR": no_color, "LC_ALL": "C"}
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


def bash_stderr(stderr_is_terminal, script=STDERR_BASH):
    """Run STDERR_BASH with exactly one of stdout and stderr on a terminal; return what reached stderr."""
    env = {**os.environ, "NO_COLOR": ""}
    main, follower = pty.openpty()
    if stderr_is_terminal:
        process = subprocess.Popen(["/bin/bash", "-c", script], env=env,
                                   stdout=subprocess.DEVNULL, stderr=follower)
    else:
        process = subprocess.Popen(["/bin/bash", "-c", script], env=env,
                                   stdout=follower, stderr=subprocess.PIPE)
    os.close(follower)
    on_terminal = read_terminal(main)
    piped = ""
    if process.stderr:
        piped = process.stderr.read().decode()
        process.stderr.close()
    if process.wait() != 0:
        raise AssertionError(f"bash exited with {process.returncode}")
    return on_terminal if stderr_is_terminal else piped


def python_output(terminal, no_color):
    stream = Terminal() if terminal else io.StringIO()
    with patch.dict(os.environ, {"NO_COLOR": no_color}), redirect_stdout(stream):
        output.title("Name")
        output.section("Name")
        output.section("Åxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        output.subsection("Sub")
        for kind in MARKS:
            print(f"  {output.mark(kind)} text")
        for color in COLORS:
            print(output.paint("x", color))
        output.dry_run("Run x to apply.")
        output.dry_run()
        output.context("a", "b c")
        output.note("note")
        output.row("warn", "Label", "detail")
        output.table_header("%-6s  %s", "A", "B")
        output.summary(0, 0, "All good")
        output.summary(2, 1, "All good")
    return stream.getvalue()


class OutputTests(unittest.TestCase):
    def test_listing_names_match_shell_palette_in_bash_and_python(self):
        palettes = {
            "Gxfxcxdxbxegedabagacad": ("\033[1;36m", "\033[35m"),
            "Exfxcxdxbxegedabagacad": ("\033[1;34m", "\033[35m"),
            "agBx": ("\033[30;46m", "\033[1;31m"),
            "xxxx": ("", ""),
            "invalid": ("", ""),
        }
        for palette, prefixes in palettes.items():
            for terminal, no_color in ((True, ""), (True, "1"), (False, "")):
                with self.subTest(palette=palette, terminal=terminal, no_color=no_color):
                    stream = Terminal() if terminal else io.StringIO()
                    env = {"LSCOLORS": palette, "NO_COLOR": no_color}
                    with patch.dict(os.environ, env):
                        names = [output.listing_name("name", kind, stream)
                                 for kind in ("folder", "symlink", "file")]
                    active = terminal and not no_color
                    expected = [
                        f"{prefix}name\033[0m" if active and prefix else "name"
                        for prefix in (*prefixes, "")
                    ]
                    self.assertEqual(names, expected)
                    script = f"""
source {str(LIB / "output.sh")!r}
for kind in folder symlink file; do out_listing_name name "$kind"; printf '\\n'; done
"""
                    with patch.dict(os.environ, env):
                        if terminal:
                            main, follower = pty.openpty()
                            process = subprocess.Popen(["/bin/bash", "-c", script], stdout=follower)
                            os.close(follower)
                            actual = read_terminal(main)
                            self.assertEqual(process.wait(), 0)
                        else:
                            actual = subprocess.run(
                                ["/bin/bash", "-c", script], capture_output=True, text=True, check=True,
                            ).stdout
                    self.assertEqual(actual, "\n".join(expected) + "\n")

    def test_bash_and_python_print_the_same_bytes(self):
        for terminal, no_color in ((True, ""), (True, "1"), (False, "")):
            with self.subTest(terminal=terminal, no_color=no_color):
                self.assertEqual(python_output(terminal, no_color), bash_output(terminal, no_color))

    def test_plain_output_carries_meaning_without_escape_codes(self):
        self.assertEqual(bash_output(False, ""), "\n".join([
            # Headings fill 60 characters, so they line up whatever the title length.
            "━ Name " + "━" * 53,
            "",
            "─ Name ─────────────────────────────────────────────────────",
            "",
            "─ Åxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx ───",
            "",
            "  ▸ Sub",
            "  ✓ text", "  ! text", "  ✗ text", "  • text",
            *["x"] * len(COLORS),
            "Dry run — nothing will be changed. Run x to apply.",
            "Dry run — nothing will be changed.",
            "a │ b c",
            "    note",
            "  ! Label                  detail",
            "  A       B",
            "",
            "─ Summary ──────────────────────────────────────────────────",
            "  ✓ All good",
            "",
            "─ Summary ──────────────────────────────────────────────────",
            "  ✗ 2 issue(s) found",
            "  ! 1 warning(s) found",
        ]) + "\n")

    def test_terminal_output_is_colored(self):
        colored = bash_output(True, "")
        self.assertIn("\033[1m─ Name ", colored)
        self.assertIn("  \033[0;32m✓\033[0m text", colored)
        self.assertIn("  \033[0;31m✗\033[0m text", colored)
        self.assertIn("\033[1m━ Name ━", colored)
        self.assertIn("  \033[1mA       B\033[0m\n", colored)

    def test_stderr_marks_follow_whether_stderr_is_a_terminal(self):
        self.assertEqual(bash_stderr(False), "! ✗\n")
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            expected = f"{output.mark('warn', Terminal())} {output.mark('fail', Terminal())}\n"
        self.assertEqual(bash_stderr(True), expected)
        self.assertIn("\033[", expected)

    def test_progress_matches_bash_and_only_appears_on_terminal_stderr(self):
        script = f"""
source {str(LIB / "output.sh")!r}
out_progress "Checking roadmap"
out_progress_done
"""
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                stream = Terminal() if terminal else io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(stream):
                    output.progress("Checking roadmap")
                    output.progress_done()
                expected = "\r\033[KChecking roadmap\r\033[K" if terminal else ""
                self.assertEqual(stream.getvalue(), expected)
                self.assertEqual(bash_stderr(terminal, script), expected)


if __name__ == "__main__":
    unittest.main()
