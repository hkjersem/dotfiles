"""Global cache cleaning with isolated paths and mocked native tools."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "clean-caches.py"
SPEC = importlib.util.spec_from_file_location("clean_caches_command", SCRIPT)
CACHES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CACHES
with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    SPEC.loader.exec_module(CACHES)


class CleanCachesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.cache = self.home / "custom-uv-cache"
        self.cache.mkdir()
        (self.cache / "data").write_bytes(b"x" * 10_000)
        home = patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        tools = patch.object(CACHES.shutil, "which", side_effect=lambda name: "/mock/" + name)
        tools.start()
        self.addCleanup(tools.stop)
        self.calls = []

    def native(self, command, **kwargs):
        self.calls.append((command, kwargs))
        value = str(self.cache) if command == ["uv", "cache", "dir"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=value, stderr="")

    def run_command(self, *args, answer="yes", native=None):
        stdout = io.StringIO()
        with patch.object(CACHES.subprocess, "run", side_effect=native or self.native), \
                patch.object(CACHES, "confirm_cleanup", return_value=answer.lower() in ("y", "yes")), contextlib.redirect_stdout(stdout):
            code = CACHES.main(list(args))
        return code, stdout.getvalue()

    def test_preview_resolves_configuration_and_never_cleans(self):
        code, text = self.run_command("--only", "uv", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertTrue(text.startswith("Dry run"))
        allocated = (self.cache / "data").stat().st_blocks * 512
        self.assertIn(f"{allocated / 1024:.1f} KiB", text)
        self.assertIn(str(self.cache), text)
        self.assertEqual([cmd for cmd, _ in self.calls], [["uv", "cache", "dir"]])
        self.assertTrue((self.cache / "data").exists())

    def test_apply_requires_explicit_selection_and_deep_acknowledgement(self):
        for args in ((), ("--only", "gradle"), ("--only", "unknown"),
                     ("--only", ""), ("--only", "go-modules")):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")):
                with self.assertRaises(SystemExit) as exc:
                    CACHES.main(list(args))
                self.assertEqual(exc.exception.code, 2)

    def test_apply_runs_native_cleaner_globally_after_confirmation(self):
        code, text = self.run_command("--only", "uv")
        self.assertEqual(code, 0, text)
        self.assertEqual([cmd for cmd, _ in self.calls],
                         [["uv", "cache", "dir"], ["uv", "cache", "dir"], ["uv", "cache", "clean"]])
        self.assertTrue(all(kwargs["cwd"] == self.home for _, kwargs in self.calls))
        self.assertIn("Observed available-space change", text)

    def test_declining_or_failed_queries_never_clean(self):
        for answer in ("", "no"):
            code, _ = self.run_command("--only", "uv", answer=answer)
            self.assertEqual(code, 0)
        code, text = self.run_command("--only", "uv", native=lambda cmd, **kw:
                                      subprocess.CompletedProcess(cmd, 3, "", "query failed"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing cleaned", text)
        self.assertFalse(any(cmd == ["uv", "cache", "clean"] for cmd, _ in self.calls))

    def test_shared_single_key_confirmation_and_closed_input(self):
        for status in (0, 1):
            with patch.object(CACHES.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], status)) as run:
                self.assertEqual(CACHES.confirm_cleanup(), status == 0)
                command = run.call_args.args[0]
                self.assertEqual(command[:2], ["/bin/bash", "-c"])
                self.assertIn('confirm_apply "Clean these global caches?" false false', command[2])
        for answer, expected in (("y", True), ("Y", True), ("n", False), ("\n", False),
                                 ("\x1b", False), ("", False)):
            script = (
                'source "$1" || exit; confirm_apply "Clean these global caches?" false false'
            )
            result = subprocess.run(
                ["/bin/bash", "-c", script, "test",
                 str(SCRIPT.parent / "package-manager/_update-lib.sh")],
                input=answer, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode == 0, expected, result.stdout)
            self.assertIn("[y/N]", result.stdout)

    def test_dry_run_never_requests_confirmation(self):
        with patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                patch.object(CACHES.subprocess, "run", side_effect=self.native), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CACHES.main(["--only", "uv", "--dry-run"]), 0)
        self.assertEqual([cmd for cmd, _ in self.calls], [["uv", "cache", "dir"]])

    def test_failed_cleaner_is_not_reported_as_success(self):
        def native(command, **kwargs):
            if command[-1] == "clean":
                return subprocess.CompletedProcess(command, 7)
            return self.native(command, **kwargs)
        code, text = self.run_command("--only", "uv", native=native)
        self.assertEqual(code, 1)
        self.assertIn("status 7", text)
        self.assertNotIn("Cleaned", text)

    def test_changed_location_after_confirmation_is_not_cleaned(self):
        count = 0
        def native(command, **kwargs):
            nonlocal count
            count += 1
            return subprocess.CompletedProcess(command, 0,
                                               str(self.cache if count == 1 else self.home / "other"), "")
        code, text = self.run_command("--only", "uv", native=native)
        self.assertEqual(code, 1)
        self.assertIn("changed after preview", text)
        self.assertEqual(count, 2)

    def test_direct_deep_cleanup_only_removes_previewed_cache(self):
        gradle = self.home / ".gradle/caches"
        gradle.mkdir(parents=True)
        (gradle / "data").write_text("rebuild")
        sentinel = self.home / ".gradle/gradle.properties"
        sentinel.write_text("keep")
        with patch.dict(os.environ, {"GRADLE_USER_HOME": str(gradle.parent)}):
            code, text = self.run_command("--only", "gradle", "--deep")
        self.assertEqual(code, 0, text)
        self.assertFalse(gradle.exists())
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(self.calls, [])

    def test_broad_symlink_and_relative_paths_are_refused(self):
        alias = self.home / "alias"
        alias.symlink_to(self.cache)
        for value in (str(self.home), "/", ".", str(alias)):
            with self.subTest(value=value):
                code, text = self.run_command("--only", "uv", native=lambda cmd, **kw:
                                              subprocess.CompletedProcess(cmd, 0, value, ""))
                self.assertEqual(code, 1)
                self.assertIn("Nothing cleaned", text)
        self.assertTrue((self.cache / "data").exists())

    def test_unreadable_sizes_block_every_cleaner_before_confirmation(self):
        def partial(path, *args, **kwargs):
            kwargs["warnings"][path / "private"] = PermissionError("denied")
            return {Path("."): 0}
        with patch.object(CACHES, "scan_sizes", side_effect=partial):
            code, text = self.run_command("--only", "uv,pip")
        self.assertEqual(code, 1)
        self.assertIn("cache size incomplete", text)
        self.assertIn("Nothing cleaned", text)
        self.assertFalse(any(cmd[-1] in ("clean", "purge") for cmd, _ in self.calls))

    def test_yarn_uses_version_specific_global_location_query(self):
        for version in ("1.22.22", "4.10.0"):
            calls = []
            def native(command, **kwargs):
                calls.append(command)
                value = version if command == ["yarn", "--version"] else str(self.cache)
                return subprocess.CompletedProcess(command, 0, value, "")
            code, text = self.run_command("--only", "yarn", "--dry-run", native=native)
            self.assertEqual(code, 0, text)
            self.assertIn(str(self.cache), text)
            expected = (["yarn", "--silent", "cache", "dir"] if version.startswith("1.")
                        else ["yarn", "config", "get", "cacheFolder"])
            self.assertEqual(calls, [["yarn", "--version"], expected])


if __name__ == "__main__":
    unittest.main()
