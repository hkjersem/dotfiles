"""Tests for the nosync command."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
ALIASES = REPO / "bash" / "aliases"


class NosyncTests(unittest.TestCase):
    def run_nosync(self, script):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".dotfiles").symlink_to(REPO, target_is_directory=True)
            return subprocess.run(
                ["/bin/bash", "-c", f"source {str(ALIASES)!r}\n{script}"],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": home},
            )

    def test_sets_attribute_and_reports_each_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first"
            second = Path(temp_dir) / "second"
            result = self.run_nosync(
                f"""
xattr() {{
    [[ "$1" == "-w" && "$2" == "com.apple.fileprovider.ignore#P" && "$3" == "1" ]]
}}
export -f xattr
nosync {str(first)!r} {str(second)!r}
"""
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"{first}: set com.apple.fileprovider.ignore#P=1",
                    f"{second}: set com.apple.fileprovider.ignore#P=1",
                ],
            )
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_inspect_reports_value_without_creating_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "existing"
            folder.mkdir()
            missing = Path(temp_dir) / "missing"
            result = self.run_nosync(
                f"""
xattr() {{
    if [[ "$1" == "-p" ]]; then
        printf '1\\n'
    elif [[ "$1" == "{folder}" ]]; then
        printf 'com.apple.fileprovider.ignore#P\\n'
    else
        return 1
    fi
}}
export -f xattr
nosync --inspect {str(folder)!r} {str(missing)!r}
"""
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"{folder}: com.apple.fileprovider.ignore#P=1",
                    f"{missing}: folder does not exist",
                ],
            )
            self.assertFalse(missing.exists())

    def test_inspect_defaults_to_current_folder_and_reports_unset_attribute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_nosync(
                f"""
cd {str(Path(temp_dir))!r}
xattr() {{ printf 'other.attribute\\n'; }}
export -f xattr
nosync --inspect
"""
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                f".: com.apple.fileprovider.ignore#P is not set",
            )

    def test_recursive_flags_find_defaults_and_prune_matches_and_symlinks(self):
        for flag in ("-r", "--recursive"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                for folder in ("project one/node_modules/.git", "project two/.git"):
                    (root / folder).mkdir(parents=True)
                (root / "linked").symlink_to(root / "project two", target_is_directory=True)
                result = self.run_nosync(
                    f"""
cd {str(root)!r}
xattr() {{
    [[ "$#" == 4 && "$1" == "-w" &&
       "$2" == "com.apple.fileprovider.ignore#P" && "$3" == "1" ]] || return 1
    printf 'write:%s\\n' "$4"
}}
export -f xattr
nosync {flag}
"""
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertCountEqual(
                    result.stdout.splitlines(),
                    [
                        "write:./project one/node_modules",
                        "./project one/node_modules: set com.apple.fileprovider.ignore#P=1",
                        "write:./project two/.git",
                        "./project two/.git: set com.apple.fileprovider.ignore#P=1",
                    ],
                )
                self.assertFalse((root / "node_modules").exists())
                self.assertFalse((root / ".git").exists())

    def test_recursive_inspect_supports_both_option_orders_and_literal_names(self):
        for options in ("-r --inspect", "--inspect --recursive"):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "project" / "build[1]*?").mkdir(parents=True)
                (root / "project" / "build1xyz").mkdir()
                result = self.run_nosync(
                    f"""
cd {str(root)!r}
xattr() {{
    if [[ "$#" == 1 ]]; then
        printf 'com.apple.fileprovider.ignore#P\\n'
    elif [[ "$#" == 3 && "$1" == "-p" ]]; then
        printf '0\\n'
    else
        return 1
    fi
}}
export -f xattr
nosync {options} 'build[1]*?'
"""
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout,
                    "./project/build[1]*?: com.apple.fileprovider.ignore#P=0\n",
                )

    def test_recursive_no_matches_reports_without_creating_folders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = self.run_nosync(f"cd {str(root)!r}\nnosync -r build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "No matching folders found below the current directory\n",
            )
            self.assertEqual(list(root.iterdir()), [])

    def test_recursive_search_failure_does_not_apply_partial_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_nosync(
                f"""
cd {temp_dir!r}
find() {{ printf './build\\0'; return 1; }}
xattr() {{ printf 'unexpected attribute operation\\n'; }}
export -f find xattr
nosync -r build
"""
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("Could not complete recursive folder search", result.stderr)
            self.assertFalse((Path(temp_dir) / "build").exists())


if __name__ == "__main__":
    unittest.main()
