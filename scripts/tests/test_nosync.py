"""Regression tests for nosync's exact attribute values and pruned traversal."""

import contextlib
import errno
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("nosync", REPO / "scripts" / "nosync.py")
nosync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nosync)


class NosyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.values = {}
        self.reads = []
        self.writes = []
        self.addCleanup(patch.stopall)
        patch.object(nosync, "get_attribute", side_effect=self.get_attribute).start()
        patch.object(nosync, "set_attribute", side_effect=self.set_attribute).start()

    def get_attribute(self, folder, attribute):
        self.assertEqual(attribute, "com.apple.fileprovider.ignore#P")
        self.reads.append(folder)
        if not Path(folder).exists():
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", folder)
        if folder not in self.values:
            raise OSError(getattr(errno, "ENOATTR", errno.ENODATA), "Attribute not found", folder)
        value = self.values[folder]
        if isinstance(value, OSError):
            raise value
        return value

    def set_attribute(self, folder, attribute, value):
        self.assertEqual(attribute, "com.apple.fileprovider.ignore#P")
        self.writes.append((folder, value))
        self.values[folder] = value

    def folder(self, path):
        (self.root / path).mkdir(parents=True, exist_ok=True)

    def run_nosync(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = nosync.main(list(args))
        return status, output.getvalue(), errors.getvalue()

    def test_defaults_create_and_mark_both_folders(self):
        status, output, errors = self.run_nosync()
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.writes, [("node_modules", b"1"), (".git", b"1")])
        self.assertTrue((self.root / "node_modules").is_dir())
        self.assertTrue((self.root / ".git").is_dir())
        self.assertEqual(output.splitlines(), [
            "node_modules: set com.apple.fileprovider.ignore#P=1",
            ".git: set com.apple.fileprovider.ignore#P=1",
        ])

    def test_inspect_defaults_to_current_folder_without_writing(self):
        status, output, errors = self.run_nosync("--inspect")
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, ".: com.apple.fileprovider.ignore#P is not set\n")
        self.assertEqual(self.writes, [])

    def test_inspect_missing_folder_reports_failure_without_creating_it(self):
        self.folder("present")
        self.values["present"] = b"1"
        status, output, errors = self.run_nosync("--inspect", "missing", "present")
        self.assertEqual(status, 1)
        self.assertIn("missing:", errors)
        self.assertEqual(output, "present: com.apple.fileprovider.ignore#P=1\n")
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(self.writes, [])

    def test_only_exact_one_skips_writing(self):
        for value in (b"1", b"1\n", b"1\n\n", b"1\x00", b"0", b"", None):
            with self.subTest(value=value):
                self.folder("build")
                self.values.clear()
                self.writes.clear()
                if value is not None:
                    self.values["build"] = value
                status, output, errors = self.run_nosync("build")
                self.assertEqual(status, 0, errors)
                if value == b"1":
                    self.assertEqual(self.writes, [])
                    self.assertIn("already marked", output)
                else:
                    self.assertEqual(self.writes, [("build", b"1")])
                    self.assertIn("set com.apple.fileprovider.ignore#P=1", output)

    def test_inspect_distinguishes_absent_empty_and_newline_values(self):
        self.folder("build")
        for value, expected in ((None, " is not set"), (b"", "="), (b"1\n", r"=1\n")):
            with self.subTest(value=value):
                self.values.clear()
                if value is not None:
                    self.values["build"] = value
                status, output, errors = self.run_nosync("--inspect", "build")
                self.assertEqual(status, 0, errors)
                self.assertEqual(output, f"build: com.apple.fileprovider.ignore#P{expected}\n")
                self.assertEqual(self.writes, [])

    def test_recursive_flags_prune_exact_matches_and_skip_symlinks(self):
        for flag in ("-r", "--recursive"):
            with self.subTest(flag=flag):
                self.folder("project one/node_modules/.git")
                self.folder("project two/.git")
                self.folder("node_modules 2/pkg/node_modules")
                link = self.root / "linked"
                if not link.is_symlink():
                    link.symlink_to(self.root / "project two", target_is_directory=True)
                self.values.clear()
                self.writes.clear()
                status, output, errors = self.run_nosync(flag)
                self.assertEqual(status, 0, errors)
                expected = [
                    "./project one/node_modules",
                    "./project two/.git",
                    "./node_modules 2/pkg/node_modules",
                ]
                self.assertCountEqual(self.writes, [(folder, b"1") for folder in expected])
                self.assertCountEqual(
                    output.splitlines(),
                    [f"{folder}: set com.apple.fileprovider.ignore#P=1" for folder in expected],
                )
                self.assertFalse((self.root / "node_modules").exists())
                self.assertFalse((self.root / ".git").exists())

    def test_only_exact_one_prunes_nonmatching_parent_or_current_folder(self):
        for parent in (".", "./archive"):
            for value in (b"1", b"1\n", b"1\n\n", b"1\x00", b"0", b"", None):
                with self.subTest(parent=parent, value=value):
                    self.folder("archive/node_modules/.git")
                    self.values.clear()
                    self.reads.clear()
                    self.writes.clear()
                    if value is not None:
                        self.values[parent] = value
                    status, output, errors = self.run_nosync("-r", "--inspect")
                    self.assertEqual(status, 0, errors)
                    if value == b"1":
                        self.assertEqual(output, f"{parent}: com.apple.fileprovider.ignore#P=1\n")
                        self.assertNotIn("./archive/node_modules", self.reads)
                    else:
                        self.assertEqual(
                            output, "./archive/node_modules: com.apple.fileprovider.ignore#P is not set\n",
                        )
                    self.assertEqual(self.writes, [])

    def test_recursive_marking_reports_marked_parent_without_visiting_children(self):
        self.folder("archive/node_modules")
        self.values["./archive"] = b"1"
        status, output, errors = self.run_nosync("-r")
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "./archive: already marked (com.apple.fileprovider.ignore#P=1)\n")
        self.assertNotIn("./archive/node_modules", self.reads)
        self.assertEqual(self.writes, [])

    def test_recursive_inspect_option_orders_and_literal_names(self):
        self.folder("project/build[1]*?")
        self.folder("project/build1xyz")
        self.values["./project/build[1]*?"] = b"0"
        for flags in (("-r", "--inspect"), ("--inspect", "--recursive")):
            with self.subTest(flags=flags):
                status, output, errors = self.run_nosync(*flags, "build[1]*?")
                self.assertEqual(status, 0, errors)
                self.assertEqual(output, "./project/build[1]*?: com.apple.fileprovider.ignore#P=0\n")
                self.assertEqual(self.writes, [])

    def test_recursive_no_matches_does_not_create_folders(self):
        status, output, errors = self.run_nosync("-r", "build")
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "No matching folders found below the current directory\n")
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.writes, [])

    def test_search_errors_never_apply_partial_discovery(self):
        self.folder("build")
        self.folder("unreadable")
        for target, operation in ((nosync, "get_attribute"), (nosync.os, "scandir")):
            with self.subTest(operation=operation):
                original = getattr(target, operation)

                def fail(folder, *args, **kwargs):
                    if folder == "./unreadable":
                        raise PermissionError(errno.EACCES, "Permission denied", folder)
                    return original(folder, *args, **kwargs)

                with patch.object(target, operation, side_effect=fail):
                    status, output, errors = self.run_nosync("-r", "build")
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertIn("Could not complete recursive folder search", errors)
                self.assertIn("./unreadable", errors)
                self.assertEqual(self.writes, [])

    def test_attribute_write_errors_report_failure_and_continue(self):
        def write(folder, attribute, value):
            if folder == "first":
                raise PermissionError(errno.EACCES, "Permission denied", folder)
            self.set_attribute(folder, attribute, value)

        with patch.object(nosync, "set_attribute", side_effect=write):
            status, output, errors = self.run_nosync("first", "second")
        self.assertEqual(status, 1)
        self.assertIn("first:", errors)
        self.assertEqual(output, "second: set com.apple.fileprovider.ignore#P=1\n")
        self.assertEqual(self.writes, [("second", b"1")])

    def test_recursive_search_uses_no_child_processes(self):
        for index in range(300):
            self.folder(f"source/{index}/build")
        with patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected subprocess")):
            status, output, errors = self.run_nosync("-r", "build")
        self.assertEqual(status, 0, errors)
        expected = [(f"./source/{index}/build", b"1") for index in range(300)]
        self.assertCountEqual(self.writes, expected)
        self.assertEqual(len(output.splitlines()), 300)


@unittest.skipUnless(sys.platform == "darwin", "requires macOS extended attributes")
class NosyncIntegrationTests(unittest.TestCase):
    def test_bash_wrapper_and_real_attributes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            home.mkdir()
            (home / ".dotfiles").symlink_to(REPO, target_is_directory=True)
            project = root / "project"
            nested = project / "archive" / "node_modules"
            nested.mkdir(parents=True)
            parent = nested.parent
            for value in (b"1", b"1\n", b"1\n\n", b"0"):
                with self.subTest(value=value):
                    # Use the external macOS tool as an independent source for the exact bytes.
                    subprocess.run(
                        ["xattr", "-wx", "com.apple.fileprovider.ignore#P", value.hex(), str(parent)],
                        check=True, capture_output=True,
                    )
                    result = subprocess.run(
                        ["/bin/bash", "-c",
                         f"source {shlex.quote(str(REPO / 'bash' / 'aliases'))}\nnosync -r --inspect"],
                        cwd=project, env={**os.environ, "HOME": str(home)},
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = (
                        "./archive: com.apple.fileprovider.ignore#P=1\n"
                        if value == b"1" else
                        "./archive/node_modules: com.apple.fileprovider.ignore#P is not set\n"
                    )
                    self.assertEqual(result.stdout, expected)
            result = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "nosync.py"), "-r"],
                cwd=project, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            stored = subprocess.check_output(
                ["xattr", "-px", "com.apple.fileprovider.ignore#P", str(nested)], text=True,
            )
            self.assertEqual(stored.strip(), "31")


if __name__ == "__main__":
    unittest.main()
