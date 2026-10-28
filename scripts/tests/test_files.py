"""Read-only filesystem reports, using isolated temporary trees."""

import contextlib
import ctypes
import errno
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "files.py"
SPEC = importlib.util.spec_from_file_location("files_command", SCRIPT)
FILES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FILES
SPEC.loader.exec_module(FILES)
import file_sizes as NATIVE


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        environment = patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache.name})
        environment.start()
        self.addCleanup(environment.stop)
        (self.root / "a.txt").write_bytes(b"same")
        (self.root / "b.TXT").write_bytes(b"same")
        (self.root / "different.txt").write_bytes(b"else")
        (self.root / ".hidden").write_bytes(b"h")
        (self.root / "zero").touch()
        (self.root / "empty").mkdir()
        (self.root / "sub" / "nested").mkdir(parents=True)
        (self.root / "sub" / "large.bin").write_bytes(b"x" * 2048)
        (self.root / "sub" / "nested" / "small.py").write_bytes(b"py")
        (self.root / "link").symlink_to(self.root / "sub", target_is_directory=True)
        (self.root / "broken").symlink_to(self.root / "missing")

    def run_report(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = FILES.main([*args, str(self.root)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return stdout.getvalue()

    def run_caches(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(Path, "home", return_value=self.root), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = FILES.main(["caches", *args])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return stdout.getvalue()

    def test_leftovers_lists_sizes_categories_without_duplicate_subtrees_or_symlinks(self):
        config = self.root / ".claude"
        (config / "tmp.inner").mkdir(parents=True)
        payload = config / "tmp.inner/data"
        payload.write_bytes(b"x" * 100_000)
        log = self.root / "task.agent.log"
        log.write_text("log")
        nested = self.root / "sub/wt-old"
        nested.mkdir()
        (nested / "data").write_text("workspace")
        (self.root / ".git").mkdir()
        (self.root / ".git/tmp.hidden").write_text("metadata")
        (self.root / "tmp.link").symlink_to(config)
        before = payload.read_bytes()
        with patch.object(Path, "open", side_effect=AssertionError("file contents read")), \
                patch.object(FILES.subprocess, "run", side_effect=AssertionError("tool executed")):
            text = self.run_report("leftovers", "--limit", "0")
        self.assertIn("Agent configuration/session data", text)
        self.assertIn("Agent log", text)
        self.assertIn("Folder", text)
        self.assertIn("File", text)
        self.assertIn(f"{payload.stat().st_blocks * 512 / 1024:.1f} KiB", text)
        self.assertLess(text.index(".claude"), text.index("task.agent.log"))
        for absent in ("tmp.inner", "tmp.link", "wt-old", "tmp.hidden", "a.txt"):
            self.assertNotIn(absent, text)
        recursive = self.run_report("leftovers", "-r", "--limit", "0")
        self.assertIn("sub/wt-old", recursive)
        self.assertIn("Temporary/workspace name match", recursive)
        self.assertNotIn("tmp.hidden", recursive)
        self.assertEqual(payload.read_bytes(), before)
        self.assertTrue((self.root / "tmp.link").is_symlink())

    def test_leftovers_age_checks_descendants_and_honours_exclusions(self):
        candidate = self.root / "tmp.old"
        (candidate / "deep").mkdir(parents=True)
        recent = candidate / "deep/recent"
        recent.write_text("new")
        for path in (recent, candidate / "deep", candidate):
            os.utime(path, (0, 0))
        os.utime(recent, (1_700_000_000, 1_700_000_000))
        with patch.object(FILES.time, "time", return_value=1_700_000_001):
            self.assertNotIn("tmp.old", self.run_report("leftovers", "--older-than", "7d"))
            text = self.run_report("leftovers", "--older-than", "7d",
                                   "--exclude", "tmp.old/deep/recent")
        self.assertIn("tmp.old", text)
        self.assertIn("1970", text)
        self.assertNotIn("tmp.old", self.run_report("leftovers", "--exclude", "tmp.*"))

    def test_leftovers_warns_unknown_age_and_does_not_treat_permission_failure_as_old(self):
        (self.root / "tmp.denied").mkdir()
        with patch.object(FILES, "latest_mtime", side_effect=PermissionError("denied")):
            text = self.run_report("leftovers")
            self.assertIn("Unknown", text)
            self.assertIn("Warnings", text)
            self.assertIn("denied", text)
            filtered = self.run_report("leftovers", "--older-than", "7d")
        self.assertIn("No matches.", filtered)
        self.assertIn("Warnings", filtered)

    def test_caches_counts_root_and_nested_allocations_and_sorts(self):
        npm = self.root / ".npm"
        npm.mkdir()
        (npm / "root").write_bytes(b"x" * 100_000)
        (npm / "nested").mkdir()
        (npm / "nested" / "data").write_bytes(b"nested")
        bun = self.root / ".bun/install/cache"
        bun.mkdir(parents=True)
        (bun / "small").write_bytes(b"x")
        expected = sum(path.lstat().st_blocks * 512 for path in
                       [npm / "root", npm / "nested/data"])
        before = {path: path.read_bytes() for path in [npm / "root", npm / "nested/data", bun / "small"]}
        with patch.object(FILES.subprocess, "run", side_effect=AssertionError("external command")):
            text = self.run_caches()
        self.assertRegex(text, rf"{re.escape(FILES.human_size(expected))}\s+npm\s+~/\.npm")
        self.assertLess(text.index("npm"), text.index("Bun"))
        self.assertNotIn("Gradle", text)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        text = self.run_caches("--limit", "1")
        self.assertIn("Showing 1 of 2", text)
        self.assertNotIn("Bun", text)
        self.assertNotIn("Showing", self.run_caches("--limit", "0"))

    def test_caches_warns_for_denied_folders_and_symlinks(self):
        npm = self.root / ".npm"
        (npm / "nested").mkdir(parents=True)
        (npm / "kept").write_bytes(b"keep")
        (self.root / ".gradle/caches").mkdir(parents=True)
        (self.root / ".bun/install").mkdir(parents=True)
        (self.root / ".bun/install/cache").symlink_to(self.root / ".gradle/caches")
        original = FILES.directory_entries

        def denied(path):
            if Path(path) in (npm / "nested", self.root / ".gradle/caches"):
                raise PermissionError(13, "Permission denied", str(path))
            return original(path)

        with patch.object(FILES, "directory_entries", side_effect=denied):
            text = self.run_caches()
        self.assertRegex(text, r"Unknown\s+npm")
        self.assertRegex(text, r"Unknown\s+Gradle")
        self.assertRegex(text, r"Unknown\s+Bun")
        self.assertIn("Incomplete report", text)
        self.assertIn("Permission denied", text)
        self.assertIn("Symlink cache skipped", text)

    def test_caches_honours_xdg_uv_location_and_empty_report(self):
        self.assertIn("No known cache folders found", self.run_caches())
        uv = Path(self.cache.name) / "uv"
        uv.mkdir()
        (uv / "cached").write_bytes(b"cache")
        text = self.run_caches()
        self.assertIn(str(uv), text)
        self.assertIn("uv", text)

    def test_caches_help_does_not_inspect_or_run_tools(self):
        with patch.object(FILES, "cache_locations", side_effect=AssertionError("scan")), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                FILES.main(["caches", "--help"])
        self.assertEqual(caught.exception.code, 0)
        for args in (["caches", "."], ["caches", "--clean"], ["caches", "-r"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    FILES.main(args)
                self.assertEqual(caught.exception.code, 2)

    def test_clean_excludes_nested_pytest_cache(self):
        (self.root / "sub/.pytest_cache").mkdir()
        (self.root / "sub/.pytest_cache/marker").write_bytes(b"cache")
        self.assertIn(".pytest_cache/marker", self.run_report("large", "-r", "--limit", "0"))
        self.assertNotIn(".pytest_cache/marker", self.run_report("large", "-r", "--clean", "--limit", "0"))

    def test_counts_include_hidden_and_links_without_following_them(self):
        for args, files, folders in (((), 5, 2), (("-r",), 7, 3)):
            with self.subTest(args=args):
                text = self.run_report("count", *args)
                self.assertRegex(text, rf"Files\s+{files}\b")
                self.assertRegex(text, rf"Folders\s+{folders}\b")
                self.assertRegex(text, r"Symlinks\s+2\b")
                self.assertRegex(text, r"Other\s+0\b")

    def test_folders_use_terminal_listing_colours_without_trailing_slash(self):
        for command in ("sizes", "empty"):
            for no_color in ("", "1"):
                with self.subTest(command=command, no_color=no_color), \
                        patch.dict(os.environ, {"LSCOLORS": "Gxfxcxdxbxegedabagacad", "NO_COLOR": no_color}), \
                        contextlib.redirect_stdout(io.StringIO()) as stdout, \
                        patch.object(FILES.sys.stdout, "isatty", return_value=True):
                    self.assertEqual(FILES.main([command, str(self.root)]), 0)
                text = stdout.getvalue()
                if no_color:
                    self.assertNotIn("\033[", text)
                else:
                    self.assertIn("\033[1;36mempty\033[0m", text)
                self.assertNotIn("empty/", text)
                if command == "empty":
                    self.assertRegex(text, r"File\s+zero\n")
                    self.assertIn("Folder", text)

    def test_sizes_cache_reuses_full_snapshot_without_inspecting_target(self):
        self.run_report("sizes", "--logical", "--limit", "1")
        (self.root / "new-folder").mkdir()
        (self.root / "sub" / "large.bin").write_bytes(b"changed")
        with patch.object(FILES, "scan_sizes", side_effect=AssertionError("rescan")), \
                patch.object(Path, "is_dir", side_effect=AssertionError("target inspected")):
            text = self.run_report("sizes", "--logical", "--cached", "--limit", "0")
        self.assertRegex(text, r"2\.0 KiB\s+sub")
        self.assertRegex(text, r"0 B\s+empty")
        self.assertNotIn("new-folder", text)
        self.assertIn("cached:", text)
        text = self.run_report("sizes", "--logical", "--limit", "0")
        self.assertIn("new-folder", text)
        self.assertNotIn("cached:", text)
        self.assertRegex(self.run_report("sizes", "--logical", "--cached"), r"9 B\s+sub")

    def test_cache_missing_expired_and_different_options_fail_without_rescan(self):
        self.run_report("sizes")
        for options in (["--direct"], ["-r"], ["--logical"], ["--clean"], ["--clean-git"],
                        ["--exclude", "sub"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(FILES.main(["sizes", "--cached", *options, str(self.root)]), 1)
            self.assertIn("run without --cached", stderr.getvalue())
        args = FILES.parser().parse_args(["sizes", str(self.root)])
        path = FILES.size_cache_path(self.root, args)
        snapshot = json.loads(path.read_text())
        with patch.object(FILES.time, "time", return_value=snapshot["created"] + 86400), \
                patch.object(FILES, "scan_sizes", side_effect=AssertionError("rescan")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(FILES.main(["sizes", "--cached", str(self.root)]), 1)
        self.assertIn("expired", stderr.getvalue())
        path.write_text("invalid JSON")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(FILES.main(["sizes", "--cached", str(self.root)]), 1)

    def test_cache_preserves_permission_warnings(self):
        original = FILES.directory_entries

        def denied(path):
            if Path(path) == self.root / "sub":
                raise PermissionError(1, "Operation not permitted", str(path))
            return original(path)

        with patch.object(FILES, "directory_entries", side_effect=denied):
            self.run_report("sizes")
        text = self.run_report("sizes", "--cached")
        self.assertIn("Incomplete report", text)
        self.assertIn("Operation not permitted", text)
        self.assertRegex(text, r"Unknown\s+sub")

    def test_sizes_cleans_only_expired_regular_snapshot_files(self):
        self.run_report("sizes")
        args = FILES.parser().parse_args(["sizes", str(self.root)])
        fresh = FILES.size_cache_path(self.root, args)
        directory = fresh.parent
        expired = directory / ("a" * 64 + ".json")
        expired.write_text("{}")
        unrelated = directory / "notes.json"
        unrelated.write_text("keep")
        target = Path(self.cache.name) / "outside.json"
        target.write_text("keep")
        link = directory / ("b" * 64 + ".json")
        link.symlink_to(target)
        nested = directory / ("c" * 64 + ".json")
        nested.mkdir()
        for path in (expired, unrelated, nested, target):
            os.utime(path, (0, 0))
        for cached in (True, False):
            with self.subTest(cached=cached):
                expired.write_text("{}")
                os.utime(expired, (0, 0))
                self.run_report("sizes", *(["--cached"] if cached else []))
                self.assertFalse(expired.exists())
                self.assertTrue(fresh.exists())
                self.assertTrue(unrelated.exists())
                self.assertTrue(link.is_symlink())
                self.assertEqual(target.read_text(), "keep")
                self.assertTrue(nested.is_dir())

    def test_cache_cleanup_errors_are_not_silenced(self):
        self.run_report("sizes")
        args = FILES.parser().parse_args(["sizes", str(self.root)])
        path = FILES.size_cache_path(self.root, args)
        os.utime(path, (0, 0))
        with patch.object(Path, "unlink", side_effect=PermissionError("cleanup denied")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(FILES.main(["sizes", "--cached", str(self.root)]), 1)
        self.assertIn("cleanup denied", stderr.getvalue())

    def test_slow_sizes_shows_progress_and_clears_before_report(self):
        events = []
        clock = iter(index * 0.3 for index in range(1000))
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(FILES.time, "monotonic", side_effect=lambda: next(clock)), \
                patch.object(FILES.sys.stdout, "isatty", return_value=True), \
                patch.object(FILES.sys.stderr, "isatty", return_value=True), \
                patch.object(FILES.output, "progress", side_effect=lambda text: events.append(text)), \
                patch.object(FILES.output, "progress_done", side_effect=lambda: events.append("cleared")), \
                patch.object(FILES.output, "context", side_effect=lambda *args: events.append("report")):
            self.assertEqual(FILES.main(["sizes", str(self.root)]), 0)
        self.assertTrue(any("entries inspected" in text for text in events), events)
        self.assertEqual(events[-2:], ["cleared", "report"])

    def test_fast_or_redirected_sizes_has_no_progress(self):
        for terminal, step in ((True, 0), (False, 3)):
            with self.subTest(terminal=terminal):
                clock = iter(index * step for index in range(1000))
                with contextlib.redirect_stdout(io.StringIO()), \
                        patch.object(FILES.sys.stdout, "isatty", return_value=terminal), \
                        patch.object(FILES.sys.stderr, "isatty", return_value=True), \
                        patch.object(FILES.time, "monotonic", side_effect=lambda: next(clock)), \
                        patch.object(FILES.output, "progress") as progress, \
                        patch.object(FILES.output, "progress_done") as done:
                    self.assertEqual(FILES.main(["sizes", str(self.root)]), 0)
                progress.assert_not_called()
                done.assert_not_called()

    def test_progress_delay_refresh_throttle_and_terminal_requirements(self):
        with patch.object(FILES.sys.stdout, "isatty", return_value=True), \
                patch.object(FILES.sys.stderr, "isatty", return_value=True), \
                patch.object(FILES.time, "monotonic", side_effect=[0, 1.99, 2, 2.1, 2.3]), \
                patch.object(FILES.output, "progress") as progress, \
                patch.object(FILES.output, "progress_done") as done:
            status = FILES.ScanProgress(self.root, True)
            for count in range(4):
                status.update(self.root / "sub", count)
            status.close()
        self.assertEqual(progress.call_count, 2)
        self.assertIn("1 entries inspected | sub", progress.call_args_list[0].args[0])
        self.assertIn("3 entries inspected | sub", progress.call_args_list[1].args[0])
        done.assert_called_once()

    def test_progress_clears_on_error_and_interruption(self):
        for error, code in ((OSError("I/O failure"), 1), (KeyboardInterrupt(), 130)):
            def failing_scan(*args, **kwargs):
                kwargs["progress"].update(self.root, 12)
                raise error

            with self.subTest(error=type(error).__name__), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(FILES.sys.stdout, "isatty", return_value=True), \
                    patch.object(FILES.sys.stderr, "isatty", return_value=True), \
                    patch.object(FILES.time, "monotonic", side_effect=[0, 3]), \
                    patch.object(FILES, "scan_sizes", side_effect=failing_scan), \
                    patch.object(FILES.output, "progress") as progress, \
                    patch.object(FILES.output, "progress_done") as done:
                self.assertEqual(FILES.main(["sizes", str(self.root)]), code)
            progress.assert_called_once()
            done.assert_called_once()

    def test_large_sort_minimum_limit_and_relative_path_exclusions(self):
        text = self.run_report("large", "-r", "--min", "1K")
        self.assertIn("2.0 KiB", text)
        self.assertIn("sub/large.bin", text)
        self.assertNotIn("a.txt", text)
        text = self.run_report("large", "-r", "--limit", "1")
        self.assertIn("sub/large.bin", text)
        self.assertIn("Showing 1 of 7", text)
        text = self.run_report("large", "-r", "--limit", "0", "--exclude", "sub/*", "--exclude", "*.TXT")
        self.assertNotIn("sub/large.bin", text)
        self.assertNotIn("small.py", text)
        self.assertNotIn("b.TXT", text)
        self.assertIn("a.txt", text)
        self.assertNotIn("Showing", text)

    def test_clean_prunes_shared_targets_but_preserves_types_and_git(self):
        (self.root / "node_modules" / "nested").mkdir(parents=True)
        (self.root / "node_modules" / "nested" / "dependency.js").write_bytes(b"dependency")
        (self.root / "sub" / "dist").mkdir()
        (self.root / "sub" / "dist" / "generated.js").touch()
        (self.root / "project.tsbuildinfo").touch()
        (self.root / ".eslintcache").touch()
        (self.root / "build").write_bytes(b"not a directory")
        (self.root / "folder.tsbuildinfo").mkdir()
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_bytes(b"ref")
        text = self.run_report("count", "-r")
        self.assertRegex(text, r"Files\s+13\b")
        self.assertRegex(text, r"Folders\s+8\b")
        text = self.run_report("count", "-r", "--clean")
        self.assertRegex(text, r"Files\s+9\b")
        self.assertRegex(text, r"Folders\s+5\b")
        self.assertIn("clean: generated artifacts excluded", text)
        text = self.run_report("large", "-r", "--clean", "--limit", "0")
        self.assertIn(".git/HEAD", text)
        self.assertIn("build", text)
        self.assertNotIn("dependency.js", text)
        self.assertNotIn("project.tsbuildinfo", text)
        text = self.run_report("count", "-r", "--clean", "--exclude", ".git")
        self.assertRegex(text, r"Files\s+8\b")
        self.assertRegex(text, r"Folders\s+4\b")
        for command in FILES.COMMANDS.keys() - {"caches"}:
            with self.subTest(command=command):
                self.assertIn("clean: generated artifacts excluded",
                              self.run_report(command, "-r", "--clean"))

    def test_clean_targets_are_loaded_from_shared_library(self):
        with patch.object(FILES.subprocess, "run", wraps=subprocess.run) as run:
            targets = FILES.clean_targets()
        self.assertIn("node_modules", targets["d"])
        self.assertIn("*.tsbuildinfo", targets["f"])
        self.assertEqual(run.call_args.args[0][-1], str(SCRIPT.parent / "lib" / "clean-targets.sh"))
        with patch.object(FILES.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, b"", b"cannot read shared list")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = FILES.main(["count", "--clean", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("could not load shared clean targets", stderr.getvalue())

    def test_clean_git_implies_clean_and_preserves_other_git_files(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_bytes(b"ref")
        (self.root / "sub" / ".git").write_bytes(b"gitdir: elsewhere")
        (self.root / ".gitignore").write_bytes(b"ignored.txt\n")
        (self.root / "ignored.txt").write_bytes(b"keep")
        (self.root / ".github").mkdir()
        (self.root / ".github" / "workflow.yml").write_bytes(b"keep")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "dependency.js").write_bytes(b"keep")
        text = self.run_report("large", "-r", "--limit", "0")
        self.assertIn(".git/HEAD", text)
        self.assertIn("sub/.git", text)
        text = self.run_report("large", "-r", "--clean-git", "--limit", "0")
        self.assertNotIn(".git/HEAD", text)
        self.assertNotIn("sub/.git", text)
        for kept in (".gitignore", ".github/workflow.yml", "ignored.txt"):
            self.assertIn(kept, text)
        self.assertNotIn("node_modules/dependency.js", text)
        self.assertIn("clean: generated artifacts excluded", text)
        self.assertIn("clean-git: .git entries excluded", text)
        text = self.run_report("large", "-r", "--clean", "--clean-git", "--limit", "0")
        self.assertNotIn("dependency.js", text)
        self.assertNotIn(".git/HEAD", text)
        self.assertNotIn("sub/.git", text)
        self.assertIn("clean: generated artifacts excluded", text)
        self.assertIn("clean-git: .git entries excluded", text)
        text = self.run_report("count", "--clean-git")
        self.assertRegex(text, r"Files\s+7\b")
        self.assertRegex(text, r"Folders\s+3\b")
        for command in FILES.COMMANDS.keys() - {"caches"}:
            with self.subTest(command=command):
                self.assertIn("clean-git: .git entries excluded",
                              self.run_report(command, "-r", "--clean-git"))

    def test_changed_uses_modification_not_creation_time_and_filters_age(self):
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                os.utime(path, (0, 0))
        os.utime(self.root / "a.txt", (1_700_000_000, 1_700_000_000))
        os.utime(self.root / "b.TXT", (1_699_000_000, 1_699_000_000))
        with patch.object(FILES.time, "time", return_value=1_700_000_001):
            text = self.run_report("changed", "-r", "--since", "7d")
        self.assertIn("a.txt", text)
        self.assertIn("november 2023, kl.", text)
        self.assertNotIn("b.TXT", text)
        text = self.run_report("changed")
        self.assertLess(text.index("a.txt"), text.index("b.TXT"))

    def test_sizes_direct_and_recursive_file_totals(self):
        text = self.run_report("sizes", "--logical")
        self.assertRegex(text, r"2\.0 KiB\s+sub")
        self.assertRegex(text, r"0 B\s+empty")
        self.assertNotRegex(text, r"(?m)^\s+\S+ B\s+link$")
        (self.root / "sub" / "nested" / "big").write_bytes(b"y" * 2048)
        text = self.run_report("sizes", "--logical")
        self.assertRegex(text, r"4\.0 KiB\s+sub\n")
        self.assertNotIn("sub/nested", text)
        text = self.run_report("sizes", "--logical", "--direct")
        self.assertRegex(text, r"2\.0 KiB\s+sub\n")
        self.assertNotIn("sub/nested", text)
        text = self.run_report("sizes", "--logical", "-r")
        self.assertRegex(text, r"4\.0 KiB\s+sub\n")
        self.assertRegex(text, r"2\.0 KiB\s+sub/nested\n")
        text = self.run_report("sizes", "--logical", "-r", "--direct")
        self.assertRegex(text, r"2\.0 KiB\s+sub\n")
        self.assertRegex(text, r"2\.0 KiB\s+sub/nested\n")
        text = self.run_report("sizes", "--logical", "-r", "--exclude", "nested")
        self.assertRegex(text, r"2\.0 KiB\s+sub")

    def test_empty_checks_actual_contents_not_filtered_contents(self):
        (self.root / "only-excluded").mkdir()
        (self.root / "only-excluded" / "ignored").touch()
        text = self.run_report("empty", "-r", "--exclude", "ignored")
        self.assertRegex(text, r"File\s+zero")
        self.assertRegex(text, r"Folder\s+empty")
        self.assertNotIn("only-excluded", text)
        self.assertNotIn("sub", text.split("─ Empty", 1)[1])

    def test_types_aggregate_case_insensitively(self):
        text = self.run_report("types", "-r")
        self.assertRegex(text, r"3\s+12 B\s+\.txt")
        self.assertRegex(text, r"2\s+1 B\s+\(no extension\)")
        self.assertRegex(text, r"1\s+2\.0 KiB\s+\.bin")

    def test_duplicates_hash_only_candidates_and_do_not_count_hardlinks(self):
        os.link(self.root / "a.txt", self.root / "hardlink")
        with patch.object(FILES, "digest", wraps=FILES.digest) as digest:
            text = self.run_report("duplicates", "-r")
        self.assertEqual(digest.call_count, 3)
        self.assertIn("1 duplicate groups", text)
        self.assertIn("potential saving: 4 B", text)
        self.assertIn("a.txt", text)
        self.assertIn("b.TXT", text)
        self.assertNotIn("different.txt", text)
        self.assertNotIn("hardlink", text)
        self.assertNotIn("zero", text)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"same")
        self.assertEqual((self.root / "b.TXT").read_bytes(), b"same")
        (self.root / "b.TXT").unlink()
        text = self.run_report("duplicates")
        self.assertIn("0 duplicate groups", text)
        self.assertIn("potential saving: 0 B", text)

    def test_duplicate_exclusions_recursion_and_limit(self):
        (self.root / "sub" / "copy.bin").write_bytes(b"x" * 2048)
        text = self.run_report("duplicates")
        self.assertIn("1 duplicate groups", text)
        text = self.run_report("duplicates", "-r", "--limit", "1")
        self.assertIn("2 duplicate groups", text)
        self.assertIn("sub/copy.bin", text)
        self.assertIn("Showing 1 of 2", text)
        self.assertNotIn("a.txt", text)
        text = self.run_report("duplicates", "-r", "--exclude", "sub")
        self.assertIn("1 duplicate groups", text)
        self.assertNotIn("copy.bin", text)

    def test_changed_file_or_symlink_swap_fails_duplicate_report(self):
        entry = FILES.Entry(self.root / "a.txt", Path("a.txt"), (self.root / "a.txt").stat())
        (self.root / "a.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(OSError, "changed"):
            FILES.digest(entry)
        (self.root / "a.txt").unlink()
        (self.root / "a.txt").symlink_to(self.root / "b.TXT")
        with self.assertRaises(OSError):
            FILES.digest(entry)

    def test_duplicates_reject_changes_during_and_after_hashing(self):
        entry = FILES.Entry(self.root / "a.txt", Path("a.txt"), (self.root / "a.txt").stat())
        original_fstat = os.fstat
        calls = 0

        def mutate_during_read(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                entry.path.write_bytes(b"changed")
            return original_fstat(fd)

        with patch.object(FILES.os, "fstat", side_effect=mutate_during_read):
            with self.assertRaisesRegex(OSError, "changed during hashing"):
                FILES.digest(entry)
        entry.path.write_bytes(b"same")
        original_digest = FILES.digest

        def mutate_after_read(candidate):
            value = original_digest(candidate)
            if candidate.relative.name == "different.txt":
                entry.path.write_bytes(b"changed")
            return value

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(FILES, "digest", side_effect=mutate_after_read), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = FILES.main(["duplicates", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("file changed during scan", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_interruption_reports_failure(self):
        with patch.object(FILES, "scan", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = FILES.main(["count", str(self.root)])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", stderr.getvalue())

    def test_read_error_is_explicit_and_no_partial_totals_are_printed(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(FILES.os, "scandir", side_effect=PermissionError("denied")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = FILES.main(["count", str(self.root)])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Error: denied", stderr.getvalue())

    def test_sizes_warns_and_continues_when_child_folder_is_denied(self):
        original = FILES.directory_entries

        def denied(path):
            if Path(path) == self.root / "sub":
                raise PermissionError(1, "Operation not permitted", str(path))
            return original(path)

        with patch.object(FILES, "directory_entries", side_effect=denied):
            text = self.run_report("sizes")
        self.assertRegex(text, r"Unknown\s+sub")
        self.assertRegex(text, r"0 B\s+empty")
        self.assertIn("Incomplete report", text)
        self.assertIn("Operation not permitted", text)

    def test_denied_nested_folders_warn_across_reports_without_claiming_empty(self):
        original = os.scandir
        original_native = FILES.directory_entries

        def denied(path):
            if Path(path) == self.root / "sub" / "nested":
                raise PermissionError(13, "Permission denied", str(path))
            return original(path)

        def denied_native(path):
            if Path(path) == self.root / "sub" / "nested":
                raise PermissionError(13, "Permission denied", str(path))
            return original_native(path)

        for command in FILES.COMMANDS.keys() - {"caches"}:
            with self.subTest(command=command), \
                    patch.object(FILES.os, "scandir", side_effect=denied), \
                    patch.object(FILES, "directory_entries", side_effect=denied_native):
                text = self.run_report(command, "-r")
            self.assertIn("Incomplete report", text)
            self.assertIn("sub/nested", text)
            if command == "sizes":
                self.assertRegex(text, r"Unknown\s+sub\n")
                self.assertRegex(text, r"Unknown\s+sub/nested\n")
            if command == "empty":
                self.assertNotRegex(text, r"Folder\s+sub/nested")
            if command == "count":
                self.assertRegex(text, r"Files\s+6\b")

    def test_denied_metadata_skips_only_that_entry(self):
        original = os.scandir

        @contextlib.contextmanager
        def denied(path):
            with original(path) as children:
                entries = []
                for child in children:
                    if child.name == "large.bin":
                        child = Mock(path=child.path, name=child.name,
                                     stat=Mock(side_effect=PermissionError(13, "Permission denied")))
                        child.name = "large.bin"
                    entries.append(child)
                yield iter(entries)

        original_native = FILES.directory_entries

        def denied_native(path):
            for name, kind, logical_size, allocated_size, error in original_native(path):
                yield (name, kind, logical_size, allocated_size,
                       errno.EACCES if name == "large.bin" else error)

        with patch.object(FILES.os, "scandir", side_effect=denied), \
                patch.object(FILES, "directory_entries", side_effect=denied_native):
            text = self.run_report("sizes", "-r")
            file_text = self.run_report("large", "-r")
        self.assertRegex(text, r"Unknown\s+sub")
        self.assertIn("sub/large.bin", text)
        self.assertIn("Permission denied", text)
        self.assertIn("Incomplete report", file_text)
        self.assertIn("Permission denied", file_text)

    def test_denied_duplicate_read_skips_file_and_keeps_other_matches(self):
        (self.root / "copy.txt").write_bytes(b"same")
        original = FILES.digest

        def denied(entry):
            if entry.relative.name == "a.txt":
                raise PermissionError(13, "Permission denied", str(entry.path))
            return original(entry)

        with patch.object(FILES, "digest", side_effect=denied):
            text = self.run_report("duplicates")
        self.assertIn("1 duplicate groups", text)
        self.assertIn("b.TXT", text)
        self.assertIn("copy.txt", text)
        self.assertIn("Incomplete report", text)
        self.assertIn("Permission denied", text)

    def test_non_permission_io_errors_still_fail(self):
        with patch.object(FILES, "directory_entries", side_effect=OSError("I/O failure")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = FILES.main(["sizes", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("I/O failure", stderr.getvalue())

    def test_invalid_arguments_and_help_never_scan(self):
        for args in (["--help"], ["large", "--help"], ["count", "--help"]):
            with self.subTest(args=args), patch.object(FILES, "scan", side_effect=AssertionError("scan")), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    FILES.main(args)
                self.assertEqual(caught.exception.code, 0)
        for args in ([], ["unknown"], ["large", "--min", "wat"], ["changed", "--since", "week"],
                     ["large", "--limit", "-1"], ["count", "--rec"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    FILES.main(args)
                self.assertEqual(caught.exception.code, 2)

    def test_cli_escapes_control_characters_and_refuses_symlink_root(self):
        (self.root / "line\nbreak").touch()
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "empty", str(self.root)],
            capture_output=True, text=True, check=True,
        )
        self.assertIn(r"line\nbreak", result.stdout)
        self.assertNotIn("line\nbreak", result.stdout)
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "count", str(self.root / "link")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink roots", result.stderr)

    def test_native_size_scan_matches_known_bytes_for_links_sparse_and_unusual_names(self):
        os.link(self.root / "sub" / "large.bin", self.root / "sub" / "hardlink")
        with (self.root / "sub" / "sparse").open("wb") as file:
            file.write(b"x")
            file.truncate(100_000)
        (self.root / "sub" / "æøå\nname").write_bytes(b"hello")
        os.mkfifo(self.root / "sub" / "pipe")
        (self.root / "sub" / "alias").symlink_to(self.root / "sub" / "nested")
        (self.root / "sub" / "outside").symlink_to(self.root)
        with patch.object(FILES.os, "scandir", side_effect=AssertionError("not a native scan")), \
                patch.object(FILES.os, "stat", side_effect=AssertionError("per-file stat")):
            totals = FILES.scan_sizes(self.root, True, False, [], logical=True)
        self.assertEqual(totals, {Path("sub"): 104_103, Path("sub/nested"): 2, Path("empty"): 0})

    def test_sizes_default_to_allocated_bytes_and_keep_logical_option(self):
        sparse = self.root / "sub" / "sparse"
        with sparse.open("wb") as file:
            file.write(b"x")
            file.truncate(100_000)
        allocated = logical = 0
        for folder, _, names in os.walk(self.root / "sub"):
            for name in names:
                info = (Path(folder) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    allocated += info.st_blocks * 512
                    logical += info.st_size
        self.assertLess(allocated, logical)
        self.assertRegex(self.run_report("sizes"), rf"{FILES.human_size(allocated)}\s+sub")
        self.assertRegex(self.run_report("sizes", "--logical"),
                         rf"{FILES.human_size(logical)}\s+sub")

    def test_native_multiple_batches_return_every_entry_once(self):
        folder = self.root / "batch"
        folder.mkdir()
        for index in range(2000):
            (folder / f"file-{index:04}").write_bytes(b"seven!!")
        original = NATIVE.LIBC.getattrlistbulk
        with patch.object(NATIVE.LIBC, "getattrlistbulk", wraps=original) as bulk:
            entries = list(NATIVE.directory_entries(folder))
        self.assertGreater(bulk.call_count, 2)
        self.assertEqual(len(entries), 2000)
        self.assertEqual({name for name, *_ in entries}, {f"file-{index:04}" for index in range(2000)})
        self.assertTrue(all(kind == "f" and logical_size == 7 and allocated_size > 0 and not error
                            for _, kind, logical_size, allocated_size, error in entries))

    def test_native_reader_matches_stat_and_does_not_follow_symlinks(self):
        expected = {}
        with os.scandir(self.root) as children:
            for entry in children:
                info = entry.stat(follow_symlinks=False)
                kind = "f" if stat.S_ISREG(info.st_mode) else "d" if stat.S_ISDIR(info.st_mode) else "other"
                expected[entry.name] = (kind, info.st_size if kind == "f" else 0,
                                        info.st_blocks * 512 if kind == "f" else 0, 0)
        self.assertEqual({name: (kind, logical_size, allocated_size, error)
                          for name, kind, logical_size, allocated_size, error
                          in NATIVE.directory_entries(self.root)}, expected)
        with self.assertRaises(OSError):
            list(NATIVE.directory_entries(self.root / "link"))

    def test_native_error_closes_directory_and_surfaces_errno(self):
        original_close = os.close
        with patch.object(NATIVE.LIBC, "getattrlistbulk", side_effect=lambda *args: (
                ctypes.set_errno(errno.EACCES) or -1)), \
                patch.object(NATIVE.os, "close", wraps=original_close) as close:
            with self.assertRaises(PermissionError):
                list(NATIVE.directory_entries(self.root))
        close.assert_called_once()

    def test_native_rejects_missing_metadata_and_malformed_records(self):
        # Fixed fields follow sys/attr.h: length, returned bitmap, errno, name ref, type, bytes.
        def record(common=NATIVE.COMMON, file_attributes=NATIVE.FILE_SIZES, error=0,
                   size=17, allocated_size=16):
            return (struct.pack("=7IiII2q", 64, common, 0, 0, file_attributes, 0, error,
                                28, 2, 1, size, allocated_size)
                    + b"x\0" + b"\0" * 6)

        for payload, expected in ((record(file_attributes=0), errno.ENOTSUP),
                                  (record(common=NATIVE.COMMON & ~8), errno.ENOTSUP),
                                  (record(common=NATIVE.COMMON & ~1), errno.EIO),
                                  (record(size=-1), errno.EIO),
                                  (record(allocated_size=-1), errno.EIO),
                                  (b"\0" * 64, errno.EIO)):
            def read(fd, attributes, buffer, length, options):
                ctypes.memmove(buffer, payload, len(payload))
                return 1

            with self.subTest(expected=expected), patch.object(NATIVE.LIBC, "getattrlistbulk", side_effect=read):
                with self.assertRaises(OSError) as caught:
                    list(NATIVE.directory_entries(self.root))
            self.assertEqual(caught.exception.errno, expected)

        payload = record(common=NATIVE.COMMON & ~8, file_attributes=0, error=errno.EACCES)
        calls = 0

        def read_denied(fd, attributes, buffer, length, options):
            nonlocal calls
            calls += 1
            ctypes.memmove(buffer, payload, len(payload))
            return 1 if calls == 1 else 0

        with patch.object(NATIVE.LIBC, "getattrlistbulk", side_effect=read_denied):
            self.assertEqual(list(NATIVE.directory_entries(self.root)), [("x", "f", 0, 0, errno.EACCES)])


if __name__ == "__main__":
    unittest.main()
