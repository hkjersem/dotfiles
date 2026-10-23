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
            "  ✓ SET       node_modules",
            "  ✓ SET       .git",
        ])

    def test_inspect_defaults_to_current_folder_without_writing(self):
        status, output, errors = self.run_nosync("--inspect")
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "  ! NOT SET   .\n")
        self.assertEqual(self.writes, [])

    def test_inspect_missing_folder_reports_failure_without_creating_it(self):
        self.folder("present")
        self.values["present"] = b"1"
        status, output, errors = self.run_nosync("--inspect", "missing", "present")
        self.assertEqual(status, 1)
        self.assertIn("  ✗ ERROR     missing  (", errors)
        self.assertEqual(output, "  ✓ MARKED    present\n")
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
                    self.assertEqual(output, "  ✓ ALREADY   build\n")
                else:
                    self.assertEqual(self.writes, [("build", b"1")])
                    self.assertEqual(output, "  ✓ SET       build\n")

    def test_inspect_distinguishes_absent_empty_and_newline_values(self):
        self.folder("build")
        for value, expected in (
            (None, "  ! NOT SET   build\n"),
            (b"", "  ! VALUE     build  (attribute=b'')\n"),
            (b"1\n", "  ! VALUE     build  (attribute=b'1\\n')\n"),
        ):
            with self.subTest(value=value):
                self.values.clear()
                if value is not None:
                    self.values["build"] = value
                status, output, errors = self.run_nosync("--inspect", "build")
                self.assertEqual(status, 0, errors)
                self.assertEqual(output, expected)
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
                    [f"  ✓ SET       {folder}" for folder in expected],
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
                        self.assertEqual(output, f"  ✓ MARKED    {parent}\n")
                        self.assertNotIn("./archive/node_modules", self.reads)
                    else:
                        self.assertEqual(
                            output, "  ! NOT SET   ./archive/node_modules\n",
                        )
                    self.assertEqual(self.writes, [])

    def test_recursive_marking_reports_marked_parent_without_visiting_children(self):
        self.folder("archive/node_modules")
        self.values["./archive"] = b"1"
        status, output, errors = self.run_nosync("-r")
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "  ✓ ALREADY   ./archive\n")
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
                self.assertEqual(output, "  ! VALUE     ./project/build[1]*?  (attribute=b'0')\n")
                self.assertEqual(self.writes, [])

    def test_recursive_output_sorts_by_path_or_puts_syncing_folders_first(self):
        for path in ("a/node_modules", "b/node_modules", "c/.git", "d/node_modules"):
            self.folder(path)
        self.values["./a/node_modules"] = b"1"
        self.values["./c/.git"] = b"0"
        self.values["./d/node_modules"] = b"1"
        find_folders = nosync.find_folders
        # Discovery order follows the file system, so reverse it to prove the output order comes from sorting.
        with patch.object(nosync, "find_folders", side_effect=lambda names: find_folders(names)[::-1]):
            for flags, expected in (
                ((), [
                    "  ✓ MARKED    ./a/node_modules",
                    "  ! NOT SET   ./b/node_modules",
                    "  ! VALUE     ./c/.git  (attribute=b'0')",
                    "  ✓ MARKED    ./d/node_modules",
                ]),
                (("--sort", "status"), [
                    "  ! NOT SET   ./b/node_modules",
                    "  ! VALUE     ./c/.git  (attribute=b'0')",
                    "  ✓ MARKED    ./a/node_modules",
                    "  ✓ MARKED    ./d/node_modules",
                ]),
            ):
                with self.subTest(flags=flags):
                    status, output, errors = self.run_nosync("-r", "--inspect", *flags)
                    self.assertEqual(status, 0, errors)
                    self.assertEqual(output.splitlines(), expected)
                    self.assertEqual(self.writes, [])

    def test_sort_status_requires_recursive_inspect(self):
        for flags in (("--inspect",), ("-r",), ()):
            with self.subTest(flags=flags):
                with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
                    nosync.main([*flags, "--sort", "status"])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--sort status requires --recursive and --inspect", errors.getvalue())
                self.assertEqual(self.writes, [])
                self.assertEqual(list(self.root.iterdir()), [])

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
        self.assertIn("  ✗ ERROR     first  (", errors)
        self.assertEqual(output, "  ✓ SET       second\n")
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

    def test_status_colors_require_a_terminal_and_respect_no_color(self):
        self.folder("marked")
        self.folder("unmarked")
        self.values["marked"] = b"1"
        for terminal, no_color, colored in (
            (False, "", False), (True, "", True), (True, "1", False),
        ):
            with self.subTest(terminal=terminal, no_color=no_color):
                output = io.StringIO()
                with (
                    patch.object(output, "isatty", return_value=terminal),
                    patch.dict(os.environ, {"NO_COLOR": no_color}),
                    contextlib.redirect_stdout(output),
                ):
                    status = nosync.main(["--inspect", "marked", "unmarked"])
                self.assertEqual(status, 0)
                if colored:
                    self.assertEqual(
                        output.getvalue(),
                        "  \033[0;32m✓ MARKED   \033[0m marked\n"
                        "  \033[1;33m! NOT SET  \033[0m unmarked\n",
                    )
                else:
                    self.assertEqual(output.getvalue(), "  ✓ MARKED    marked\n  ! NOT SET   unmarked\n")
                self.assertEqual(self.writes, [])

    def test_error_color_uses_stderr_terminal_not_stdout(self):
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.object(output, "isatty", return_value=False),
            patch.object(errors, "isatty", return_value=True),
            patch.dict(os.environ, {"NO_COLOR": ""}),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            status = nosync.main(["--inspect", "missing"])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("  \033[0;31m✗ ERROR    \033[0m missing  (", errors.getvalue())
        self.assertFalse((self.root / "missing").exists())


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
                        "  ✓ MARKED    ./archive\n"
                        if value == b"1" else
                        "  ! NOT SET   ./archive/node_modules\n"
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
