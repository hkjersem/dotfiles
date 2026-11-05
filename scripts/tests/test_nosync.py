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
        self.assertEqual(output, "    No matching folders found below the current directory\n")
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.writes, [])

    def test_gitignore_inspects_and_marks_ignored_folders_with_exclusions(self):
        self.folder(".venv/lib/python")
        self.folder("src/__pycache__")
        self.folder("src/node_modules/pkg")
        self.folder(".data/cache")
        self.folder("logs")
        (self.root / ".gitignore").write_text(
            ".venv/\n__pycache__/\nnode_modules/\n.data/\n*.py[cod]\n",
            encoding="utf-8",
        )

        status, output, errors = self.run_nosync(
            "--gitignore", "--exclude", ".data", "--inspect",
        )
        self.assertEqual(status, 0, errors)
        expected = [
            "  ! NOT SET   ./.venv",
            "  ! NOT SET   ./src/__pycache__",
            "  ! NOT SET   ./src/node_modules",
        ]
        self.assertEqual(output.splitlines(), expected)
        self.assertEqual(self.writes, [])
        self.assertNotIn("./.venv/lib", self.reads)
        self.assertNotIn("./.data", self.reads)

        status, output, errors = self.run_nosync(
            "--gitignore", ".gitignore", "--exclude", ".data",
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            self.writes,
            [("./.venv", b"1"), ("./src/__pycache__", b"1"), ("./src/node_modules", b"1")],
        )
        self.assertEqual(
            output.splitlines(),
            [
                "  ✓ SET       ./.venv",
                "  ✓ SET       ./src/__pycache__",
                "  ✓ SET       ./src/node_modules",
            ],
        )

    def test_gitignore_ignores_global_excludes_file(self):
        self.folder(".global-cache")
        self.folder("project/.global-cache")
        (self.root / ".gitignore").write_text("nothing\n", encoding="utf-8")
        global_ignore = self.root / "config" / "git" / "ignore"
        global_ignore.parent.mkdir(parents=True)
        global_ignore.write_text(".global-cache/\n", encoding="utf-8")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}):
            status, output, errors = self.run_nosync("--gitignore", "--inspect")

        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "    No matching folders found below the current directory\n")
        self.assertEqual(self.writes, [])

    def test_gitignore_exclusion_inside_ignored_parent_is_not_marked(self):
        self.folder(".data/keep")
        self.folder(".data/cache")
        (self.root / ".gitignore").write_text(".data/\n", encoding="utf-8")

        status, output, errors = self.run_nosync(
            "--gitignore", "--exclude", ".data/keep", "--inspect",
        )

        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "  ! NOT SET   ./.data/cache\n")
        self.assertNotIn("./.data/keep", self.reads)
        self.assertNotIn("./.data\n", output)

    def test_gitignore_exclusion_inside_already_marked_parent_fails_before_writes(self):
        self.folder(".data/keep")
        (self.root / ".gitignore").write_text(".data/\n", encoding="utf-8")
        self.values["./.data"] = b"1"

        status, output, errors = self.run_nosync(
            "--gitignore", "--exclude", ".data/keep",
        )

        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("cannot honor --exclude inside already marked folder", errors)
        self.assertEqual(self.writes, [])

    def test_clean_targets_uses_shared_directory_list_recursively(self):
        self.folder(".venv/lib")
        self.folder("nested/venv/lib")
        self.folder("nested/__pycache__")
        self.folder(".git/venv")
        self.folder("ordinary")

        status, output, errors = self.run_nosync("--clean-targets", "--inspect")
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            output.splitlines(),
            [
                "  ! NOT SET   ./.venv",
                "  ! NOT SET   ./nested/__pycache__",
                "  ! NOT SET   ./nested/venv",
            ],
        )
        self.assertEqual(self.writes, [])
        self.assertNotIn("./.venv/lib", self.reads)
        self.assertNotIn("./.git", self.reads)

        status, output, errors = self.run_nosync("--clean-targets")
        self.assertEqual(status, 0, errors)
        self.assertCountEqual(
            self.writes,
            [("./.venv", b"1"), ("./nested/__pycache__", b"1"), ("./nested/venv", b"1")],
        )

    def test_clean_targets_can_exclude_exact_relative_paths(self):
        self.folder(".venv/lib")
        self.folder("nested/venv/lib")
        self.folder("nested/__pycache__")
        status, output, errors = self.run_nosync(
            "--clean-targets", "--exclude", ".venv", "--inspect",
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            output.splitlines(),
            ["  ! NOT SET   ./nested/__pycache__", "  ! NOT SET   ./nested/venv"],
        )
        self.assertNotIn("./.venv", self.reads)

    def test_clean_target_containing_exclusion_is_not_marked(self):
        self.folder(".venv/keep/__pycache__")
        self.folder(".venv/cache/__pycache__")

        status, output, errors = self.run_nosync(
            "--clean-targets", "--exclude", ".venv/keep", "--inspect",
        )

        self.assertEqual(status, 0, errors)
        self.assertEqual(output, "  ! NOT SET   ./.venv/cache/__pycache__\n")
        self.assertNotIn("./.venv/keep", self.reads)
        self.assertNotIn("./.venv\n", output)

    def test_clean_targets_can_be_combined_with_exact_folder_names(self):
        self.folder("nested/venv")
        self.folder("nested/custom-cache")
        status, output, errors = self.run_nosync(
            "--clean-targets", "--recursive", "custom-cache", "--inspect",
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            output.splitlines(),
            ["  ! NOT SET   ./nested/custom-cache", "  ! NOT SET   ./nested/venv"],
        )

    def test_gitignore_and_clean_targets_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
            nosync.main(["--gitignore", "--clean-targets"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--gitignore and --clean-targets cannot be combined", errors.getvalue())

    def test_gitignore_search_errors_do_not_mark_any_folders(self):
        self.folder(".venv")
        status, output, errors = self.run_nosync("--gitignore", "missing.ignore")
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("Could not complete recursive folder search", errors)
        self.assertEqual(self.writes, [])

    def test_exclude_requires_gitignore_or_clean_targets(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
            nosync.main(["--exclude", ".data"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--exclude requires --gitignore or --clean-targets", errors.getvalue())
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
