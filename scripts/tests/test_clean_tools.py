"""Fixture-only checks for clean copies and cleanup target selection."""

import os
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]


class CleanToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="clean-tools-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "project with spaces"
        self.source.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.temp = self.root / "tmp"
        self.temp.mkdir()
        self.trace = self.root / "deletion-arguments"
        self.env = dict(os.environ, TMPDIR=str(self.temp),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CLEAN_TEST_TRACE=str(self.trace))
        for key in ("BASH_ENV", "ENV"):
            self.env.pop(key, None)
        self.write("source.txt", "keep")

    def write(self, relative, text="generated"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def mock(self, command, body):
        path = self.bin / command
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def run_script(self, script, *args, answers=""):
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts" / script), *map(str, args)],
            cwd=self.source, env=self.env, input=answers, text=True,
            capture_output=True, timeout=20,
        )

    def mock_deletion(self):
        self.mock("rm", '''if [[ "$1" == -rf ]]; then
    printf '%s\\0' "${@:3}" >> "$CLEAN_TEST_TRACE"
else
    /bin/rm "$@"
fi
''')

    def selected_targets(self):
        if not self.trace.exists():
            return set()
        return set(self.trace.read_bytes().decode().rstrip("\0").split("\0"))

    def set_submodules(self, *paths):
        for index, path in enumerate(paths):
            subprocess.run(
                ["git", "config", "--file", str(self.source / ".gitmodules"),
                 "submodule.test" + str(index) + ".path", path],
                env=self.env, check=True, capture_output=True, text=True,
            )

    def test_copy_excludes_shared_artifacts_and_preserves_source(self):
        for path in ("node_modules/module/file", ".output/file", "nested/build/file",
                     "playwright-report/file", "types.tsbuildinfo", ".eslintcache", ".pytest_cache/cache"):
            self.write(path)
        self.write(".git/config", "metadata")
        self.write("nested/source.txt", "nested")
        (self.source / "source-link").symlink_to("source.txt")
        destination = self.root / "copy"
        result = self.run_script("copy-clean.sh", destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination / "source.txt").read_text(), "keep")
        self.assertTrue((destination / ".git/config").exists())
        self.assertTrue((destination / "source-link").is_symlink())
        self.assertTrue((destination / "nested/source.txt").exists())
        for relative in ("node_modules", ".output", "nested/build", "playwright-report",
                         "types.tsbuildinfo", ".eslintcache", ".pytest_cache"):
            self.assertFalse((destination / relative).exists(), relative)
            self.assertTrue((self.source / relative).exists(), relative)

    def test_copy_keep_name_and_ignore_git(self):
        self.write(".git/config")
        self.write("nested/.git/config")
        parent = self.root / "copies"
        parent.mkdir()
        result = self.run_script("copy-clean.sh", "--keep-name", "--ignore-git", parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = parent / self.source.name
        self.assertTrue((destination / "source.txt").exists())
        self.assertFalse((destination / ".git").exists())
        self.assertFalse((destination / "nested/.git").exists())

    def test_zip_default_preserves_folder_name_and_excludes_artifacts(self):
        self.write("node_modules/file")
        result = self.run_script("copy-clean.sh", "--zip")
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = self.source / (self.source.name + ".zip")
        with zipfile.ZipFile(archive) as zipped:
            names = zipped.namelist()
            self.assertIn(self.source.name + "/source.txt", names)
            self.assertFalse(any("node_modules" in name for name in names))
            self.assertFalse(any(name.endswith(".zip") for name in names))
        self.assertFalse(list(self.temp.iterdir()))

    def test_copy_refuses_existing_or_nested_destination(self):
        existing = self.root / "existing"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_text("keep")
        for destination in (existing, self.source / "nested-copy"):
            with self.subTest(destination=destination):
                result = self.run_script("copy-clean.sh", destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Error:", result.stderr)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse((self.source / "nested-copy").exists())

    def test_zip_explicit_source_and_destination_directory(self):
        destination = self.root / "archives"
        destination.mkdir()
        result = self.run_script("copy-clean.sh", "--zip", self.source, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / (self.source.name + ".zip")).exists())
        self.assertFalse(list(self.temp.iterdir()))

    def test_copy_traversal_failure_does_not_report_success(self):
        self.mock("find", "printf 'Simulated traversal failure\\n' >&2\nexit 7\n")
        for zip_mode in (False, True):
            with self.subTest(zip_mode=zip_mode):
                destination = self.root / ("failed.zip" if zip_mode else "failed-copy")
                args = ("--zip", destination) if zip_mode else (destination,)
                result = self.run_script("copy-clean.sh", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("copy aborted", result.stderr)
                self.assertNotIn("Copied clean tree", result.stdout)
                self.assertNotIn("Created clean archive", result.stdout)
                self.assertFalse(destination.exists())
                self.assertFalse(list(self.temp.iterdir()))

    def test_wipe_keeps_newline_paths_as_single_targets(self):
        self.mock_deletion()
        self.write("parent\nother/build/file")
        self.write("nested/cache\nname.tsbuildinfo")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selected_targets(), {
            "./parent\nother/build", "./nested/cache\nname.tsbuildinfo",
        })
        self.assertTrue((self.source / "source.txt").exists())
        self.assertFalse(list(self.temp.iterdir()))

    def test_wipe_preserves_submodules_with_spaces_globs_and_newlines(self):
        self.mock_deletion()
        paths = ("vendor/module with spaces", "vendor/module[1]", "vendor/module\nname",
                 "build/nested-submodule")
        for path in paths:
            self.write(path + "/build/generated")
        self.set_submodules(*paths)
        self.write(".git/build/metadata")
        self.write("node_modules/module")
        self.write("nested/.oxlintcache")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selected_targets(), {"./node_modules", "./nested/.oxlintcache"})

    def test_wipe_refusal_deletes_nothing(self):
        self.mock_deletion()
        self.write("build/file")
        result = self.run_script("wipe-clean.sh", answers="n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.selected_targets())
        self.assertTrue((self.source / "build/file").exists())
        self.assertNotIn("Available on current filesystem", result.stdout)

    def test_wipe_dry_run_never_prompts_or_deletes_even_with_yes_input(self):
        self.mock_deletion()
        self.write("build/file")
        result = self.run_script("wipe-clean.sh", "--dry-run", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("Dry run"))
        self.assertIn("build", result.stdout)
        self.assertNotIn("Proceed?", result.stdout)
        self.assertFalse(self.selected_targets())
        self.assertTrue((self.source / "build/file").exists())

    def test_wipe_sort_changes_preview_only_and_reports_disk_space(self):
        self.mock_deletion()
        self.write("build/file", "small")
        self.write("dist/file", "x" * 100_000)
        default = self.run_script("wipe-clean.sh", answers="n\n")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertLess(default.stdout.index("build"), default.stdout.index("dist"))
        sorted_result = self.run_script("wipe-clean.sh", "--sort", "size", answers="y\n")
        self.assertEqual(sorted_result.returncode, 0, sorted_result.stderr)
        self.assertLess(sorted_result.stdout.index("dist"), sorted_result.stdout.index("build"))
        self.assertEqual(self.selected_targets(), {"./build", "./dist"})
        self.assertIn("Available on current filesystem", sorted_result.stdout)
        self.assertIn("Observed change:", sorted_result.stdout)
        for args in (("--sort",), ("--sort", "invalid")):
            with self.subTest(args=args):
                result = self.run_script("wipe-clean.sh", *args, answers="y\n")
                self.assertEqual(result.returncode, 2)

    def test_wipe_older_than_checks_every_descendant_and_preserves_links(self):
        self.mock_deletion()
        old = self.write("build/deep/file")
        recent = self.write("dist/deep/file")
        link = self.source / "build/external"
        link.symlink_to(self.source / "source.txt")
        stamp = time.time() - 10 * 86400
        for base in ("build", "dist"):
            for path in sorted((self.source / base).rglob("*"), reverse=True):
                os.utime(path, (stamp, stamp), follow_symlinks=False)
            os.utime(self.source / base, (stamp, stamp))
        os.utime(recent, None)
        result = self.run_script("wipe-clean.sh", "--older-than", "7d", "--sort", "size", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selected_targets(), {"./build"})
        self.assertIn("build", result.stdout)
        self.assertNotIn("dist", result.stdout)
        self.assertTrue(old.exists())  # rm is mocked.
        self.assertTrue((self.source / "source.txt").exists())

    def test_wipe_age_recheck_aborts_if_target_changes_after_preview(self):
        self.mock_deletion()
        self.write("build/file")
        stamp = time.time() - 10 * 86400
        for path in (self.source / "build/file", self.source / "build"):
            os.utime(path, (stamp, stamp))
        self.mock("python3", f'''case "$*" in *--check-age*) touch build/file ;; esac
exec "{sys.executable}" "$@"
''')
        result = self.run_script("wipe-clean.sh", "--older-than", "7d", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target was modified recently", result.stderr)
        self.assertFalse(self.selected_targets())

    def test_wipe_age_empty_selection_cancellation_and_invalid_age(self):
        self.mock_deletion()
        self.write("build/file")
        result = self.run_script("wipe-clean.sh", "--older-than", "7d", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing to clean.", result.stdout)
        self.assertNotIn("Estimated total", result.stdout)
        for path in (self.source / "build/file", self.source / "build"):
            os.utime(path, (0, 0))
        cancelled = self.run_script("wipe-clean.sh", "--older-than", "7d", answers="n\n")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.assertIn("build", cancelled.stdout)
        for args in (("--older-than",), ("--older-than", "-7d"), ("--older-than", "garbage")):
            result = self.run_script("wipe-clean.sh", *args, answers="y\n")
            self.assertEqual(result.returncode, 2)
            self.assertIn("Error:", result.stderr)
        self.assertFalse(self.selected_targets())
        self.assertFalse(list(self.temp.iterdir()))

    def test_age_preview_permission_failure_does_not_write_a_deletion_plan(self):
        target = self.write("build/file")
        spec = importlib.util.spec_from_file_location("wipe_age_preview", REPO / "scripts/wipe-clean-preview.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(REPO / "scripts"), *sys.path]):
            spec.loader.exec_module(module)
        selected = self.root / "selected"
        stream = io.TextIOWrapper(io.BytesIO(os.fsencode(target.parent) + b"\0"))
        with patch.object(module.sys, "stdin", stream), \
                patch.object(module, "latest_mtime", side_effect=PermissionError("age unreadable")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = module.main(["--older-than", "7d", "--targets-output", str(selected)])
        self.assertEqual(code, 1)
        self.assertIn("age unreadable", stderr.getvalue())
        self.assertFalse(selected.exists())
        self.assertTrue(target.exists())

    def test_disk_space_comparison_uses_metadata_only_and_handles_decreases(self):
        spec = importlib.util.spec_from_file_location("wipe_preview", REPO / "scripts/wipe-clean-preview.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(REPO / "scripts"), *sys.path]):
            spec.loader.exec_module(module)
        for available, expected in ((3072, "+2.0 KiB"), (0, "-1.0 KiB")):
            with self.subTest(available=available), \
                    patch.object(module.shutil, "disk_usage", return_value=type("Usage", (), {"free": available})()), \
                    patch.object(module, "scan_sizes", side_effect=AssertionError("rescan")), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(module.main(["--space-after", "1024"]), 0)
            self.assertIn(expected, stdout.getvalue())
        with patch.object(module.shutil, "disk_usage", side_effect=OSError("disk query failed")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(module.main(["--available-bytes"]), 1)
        self.assertIn("disk query failed", stderr.getvalue())

    def test_wipe_previews_allocations_and_pytest_before_confirmation(self):
        self.mock_deletion()
        build = self.write("build/file", "x" * 100_000)
        cache = self.write(".pytest_cache/nested/cache", "cache")
        total = (build.stat().st_blocks + cache.stat().st_blocks) * 512
        result = self.run_script("wipe-clean.sh", answers="n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{total / 1024:.1f} KiB", result.stdout)
        self.assertIn("Estimated total", result.stdout)
        self.assertIn(".pytest_cache", result.stdout)
        self.assertFalse(self.selected_targets())
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selected_targets(), {"./build", "./.pytest_cache"})

    def test_preview_failure_aborts_deletion(self):
        self.mock_deletion()
        self.write("build/file")
        self.mock("python3", "printf 'Simulated sizing failure\\n' >&2\nexit 1\n")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing was deleted", result.stderr)
        self.assertFalse(self.selected_targets())

    def test_wipe_with_no_targets_is_a_successful_noop(self):
        self.mock_deletion()
        self.write(".gitmodules", "")
        self.write("build", "a source file, not a build directory")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("Nothing to clean.", result.stdout)
        self.assertFalse(self.selected_targets())
        self.assertFalse(list(self.temp.iterdir()))

    def test_wipe_traversal_failure_deletes_nothing(self):
        self.mock_deletion()
        self.mock("find", "printf './build\\0'\nexit 7\n")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing was deleted", result.stderr)
        self.assertFalse(self.selected_targets())

    def test_wipe_invalid_submodule_config_deletes_nothing(self):
        self.mock_deletion()
        self.write(".gitmodules", "[invalid\n")
        self.write("build/file")
        result = self.run_script("wipe-clean.sh", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not read submodule paths", result.stderr)
        self.assertFalse(self.selected_targets())


if __name__ == "__main__":
    unittest.main()
