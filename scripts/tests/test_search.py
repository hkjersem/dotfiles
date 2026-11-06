"""Focused ripgrep-backed search and snippet behavior."""

import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        home = root / "home"
        home.mkdir()
        (home / ".dotfiles").symlink_to(REPO, target_is_directory=True)
        self.project = root / "project"
        self.project.mkdir()
        self.env = {
            **os.environ,
            "HOME": str(home),
            "NO_COLOR": "1",
            "RIPGREP_CONFIG_PATH": "",
            "PATH": "/bin:/usr/bin:" + os.environ["PATH"],
        }

    def run_search(self, *args, expected=0, input_text=None):
        result = subprocess.run(
            ["/bin/bash", "-c", 'source "$1/bash/aliases"; shift; search "$@"',
             "search-test", str(REPO), *args],
            cwd=self.project, env=self.env, input=input_text,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_clean_targets_and_git_stay_excluded_with_path_globs(self):
        for target in ("node_modules", "dist", "build", ".cache", "__pycache__"):
            directory = self.project / "nested" / target
            directory.mkdir(parents=True)
            (directory / "generated.py").write_text("needle\n")
        (self.project / "nested" / ".eslintcache").write_text("needle\n")
        (self.project / ".git").mkdir()
        (self.project / ".git" / "hidden.py").write_text("needle\n")
        (self.project / "source.py").write_text("needle\n")

        result = self.run_search("--hidden", "-g", "*.py", "needle", ".")
        self.assertEqual(result.stdout, "./source.py:1:needle\n")
        all_files = self.run_search("--hidden", "needle", ".")
        self.assertEqual(all_files.stdout, "./source.py:1:needle\n")

    def test_no_clean_includes_targets_but_never_git(self):
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "generated.py").write_text("needle\n")
        (self.project / ".git").mkdir()
        (self.project / ".git" / "hidden.py").write_text("needle\n")
        (self.project / ".eslintcache").write_text("needle\n")
        (self.project / "source.py").write_text("needle\n")

        result = self.run_search("--no-clean", "--hidden", "needle", ".")
        self.assertIn("./source.py:1:needle\n", result.stdout)
        self.assertIn("./node_modules/generated.py:1:needle\n", result.stdout)
        self.assertIn("./.eslintcache:1:needle\n", result.stdout)
        self.assertNotIn(".git/", result.stdout)
        late_flag = self.run_search("--hidden", "needle", ".", "--no-clean")
        self.assertIn("./node_modules/generated.py:1:needle\n", late_flag.stdout)
        self.assertNotIn(".git/", late_flag.stdout)

    def test_user_exclusion_globs_do_not_reenable_clean_targets(self):
        (self.project / "source.py").write_text("needle\n")
        (self.project / "test_source.py").write_text("needle\n")
        (self.project / "dist").mkdir()
        (self.project / "dist" / "generated.py").write_text("needle\n")

        result = self.run_search("-g", "*.py", "-g", "!test_*", "needle", ".")
        self.assertEqual(result.stdout, "./source.py:1:needle\n")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "generated.py").write_text("needle\n")
        self.run_search("-g", "node_modules/**", "needle", ".", expected=1)

    def test_no_clean_used_as_glob_value_does_not_disable_exclusions(self):
        (self.project / "--no-clean").write_text("needle\n")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "generated.py").write_text("needle\n")

        result = self.run_search("-g", "--no-clean", "needle", ".")
        self.assertEqual(result.stdout, "./--no-clean:1:needle\n")

    def test_separator_accepts_dash_prefixed_pattern_and_no_clean_as_text(self):
        (self.project / "source.py").write_text("--no-clean\n")
        result = self.run_search("--", "--no-clean", "source.py")
        self.assertEqual(result.stdout, "source.py:1:--no-clean\n")

    def test_short_flags_and_search_options_are_forwarded(self):
        (self.project / "source.py").write_text("FOO foo foobar\n")
        result = self.run_search("-in", "foo", "source.py")
        self.assertEqual(result.stdout, "source.py:1:FOO foo foobar\n")
        result = self.run_search("-Fw", "foo", "source.py")
        self.assertEqual(result.stdout, "source.py:1:FOO foo foobar\n")

    def test_long_lines_show_each_full_match_and_short_lines_are_preserved(self):
        line = "x" * 300 + "needle" + "y" * 500 + "needle" + "z" * 300
        (self.project / "bundle.js").write_text("short needle\n" + line + "\n")
        result = self.run_search("-M", "60", "needle", "bundle.js")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "bundle.js:1:short needle")
        self.assertEqual(sum(value.count("needle") for value in lines), 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(value.split(":", 2)[-1]) <= 70 for value in lines[1:]))

    def test_colon_hyphen_suffix_and_matches_wider_than_snippet_are_kept(self):
        (self.project / "punctuation.txt").write_text(
            "x" * 300 + "needle:module-name-with-hyphen:continued" + "y" * 300 + "\n"
        )
        suffix = self.run_search("-M", "100", "needle", "punctuation.txt").stdout
        self.assertIn("needle:module-name-with-hyphen:continued", suffix)

        match = "needle:module-name-with-hyphen-" * 8
        (self.project / "long-match.txt").write_text("x" * 300 + match + "y" * 300 + "\n")
        full_match = self.run_search("-F", "-M", "40", match, "long-match.txt").stdout
        self.assertIn(match, full_match)
        self.assertEqual(full_match.count(match), 1)

    def test_unicode_and_invalid_utf8_snippets_keep_match_offsets(self):
        (self.project / "unicode.txt").write_text("ø" * 300 + "needle" + "æ" * 300 + "\n")
        (self.project / "bytes.txt").write_bytes(b"\xff" * 300 + b"needle" + b"\xfe" * 300 + b"\n")
        result = subprocess.run(
            ["/usr/bin/python3", str(REPO / "scripts/search.py"), "-M", "40", "needle", "."],
            cwd=self.project, env=self.env, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout.count(b"needle"), 2)
        self.assertIn(("ø" * 17 + "needle" + "æ" * 17).encode(), result.stdout)
        self.assertIn(b"\xff" * 17 + b"needle" + b"\xfe" * 17, result.stdout)
        self.assertLess(max(map(len, result.stdout.splitlines())), 100)

    def test_zero_width_and_no_trim_preserve_full_matching_line(self):
        (self.project / "source.py").write_text(" \t  needle  value \t\n")
        self.assertEqual(
            self.run_search("-n", "needle", "source.py").stdout,
            "source.py:1:needle  value \t\n",
        )
        self.assertEqual(
            self.run_search("-M", "0", "--no-trim", "needle", "source.py").stdout,
            "source.py:1: \t  needle  value \t\n",
        )
        (self.project / "spaces.txt").write_text("   needle\n")
        self.assertEqual(
            self.run_search("-F", "  ", "spaces.txt").stdout,
            "spaces.txt:1:   needle\n",
        )

    def test_context_lines_keep_native_context_markers(self):
        (self.project / "source.py").write_text("before\nneedle\nafter\n")
        result = self.run_search("-C", "1", "needle", "source.py")
        self.assertEqual(
            result.stdout,
            "source.py:1-before\nsource.py:2:needle\nsource.py:3-after\n",
        )
        result = self.run_search("-C", "1", "-A", "0", "needle", "source.py")
        self.assertEqual(result.stdout, "source.py:1-before\nsource.py:2:needle\n")

    def test_terminal_uses_filename_heading_and_line_number(self):
        (self.project / "bundle.js").write_text("x" * 300 + "needle\n")
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                ["/usr/bin/python3", str(REPO / "scripts/search.py"), "needle", "."],
                cwd=self.project, env=self.env, stdout=slave, stderr=subprocess.PIPE, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            text = os.read(master, 16384).decode("utf-8").replace("\r\n", "\n")
        finally:
            os.close(master)
            os.close(slave)
        self.assertIn("./bundle.js\n", text)
        self.assertIn("1:... ", text)
        self.assertIn("needle", text)
        self.assertNotIn("\x1b[", text)

    def test_terminal_without_paths_searches_current_directory(self):
        (self.project / "source.py").write_text("needle\n")
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                ["/usr/bin/python3", str(REPO / "scripts/search.py"), "needle"],
                cwd=self.project, env=self.env, stdin=slave, stdout=slave,
                stderr=subprocess.PIPE, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            text = os.read(master, 16384).decode("utf-8").replace("\r\n", "\n")
        finally:
            os.close(master)
            os.close(slave)
        self.assertIn("./source.py\n", text)
        self.assertIn("1:needle", text)

    def test_terminal_colors_cover_headings_numbers_and_each_snippet_match(self):
        (self.project / "source.py").write_text(
            "short needle\n" + "x" * 300 + "needle" + "y" * 400 + "needle\n"
        )
        master, slave = pty.openpty()
        env = dict(self.env)
        env.pop("NO_COLOR", None)
        try:
            result = subprocess.run(
                ["/usr/bin/python3", str(REPO / "scripts/search.py"), "needle", "."],
                cwd=self.project, env=env, stdout=slave, stderr=subprocess.PIPE, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            text = os.read(master, 16384).decode().replace("\r\n", "\n")
        finally:
            os.close(master)
            os.close(slave)
        self.assertIn("\x1b[0;36m./source.py\x1b[0m", text)
        self.assertIn("\x1b[0;32m1\x1b[0m:", text)
        self.assertEqual(text.count("\x1b[0;31mneedle\x1b[0m"), 3)

    def test_stdin_search_no_matches_and_unsupported_options(self):
        result = self.run_search("needle", input_text="x" * 300 + "needle\n")
        self.assertTrue(result.stdout.startswith("1:... "))
        self.assertIn("needle", result.stdout)
        (self.project / "source.py").write_text("nothing here\n")
        self.run_search("missing", "source.py", expected=1)
        unsupported = self.run_search("--json", "needle", expected=2)
        self.assertIn("See search --help for supported options", unsupported.stderr)

    def test_broken_pipe_does_not_print_a_python_traceback(self):
        (self.project / "many.txt").write_text("needle\n" * 10000)
        process = subprocess.Popen(
            ["/usr/bin/python3", str(REPO / "scripts/search.py"), "needle", "many.txt"],
            cwd=self.project, env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        process.stdout.read(1)
        process.stdout.close()
        process.wait(timeout=30)
        error = process.stderr.read()
        process.stderr.close()
        self.assertNotIn(b"BrokenPipeError", error)
        self.assertNotIn(b"Traceback", error)

    def test_terminal_controls_in_paths_matches_and_context_are_escaped(self):
        path = "spoof\x1b[2J.txt"
        (self.project / path).write_text("before\x07\nneedle\x1b]52;c;payload\x07\n")
        result = self.run_search("-C", "1", "needle", path)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)
        self.assertIn(r"spoof\x1b[2J.txt:1-before\x07", result.stdout)
        self.assertIn(r"needle\x1b]52;c;payload\x07", result.stdout)


if __name__ == "__main__":
    unittest.main()
