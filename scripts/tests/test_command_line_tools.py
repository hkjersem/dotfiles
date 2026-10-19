"""Isolated tests for the Command Line Tools check and update."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "scripts/lib/command-line-tools.sh"
MOCK_TOOL = r'''
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["CLT_TEST_STATE"])
marker = Path(os.environ["DOTFILES_CLT_MARKER"])
with open(os.environ["CLT_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, args, marker.exists()]) + "\n")
if name == "sudo":
    os.execvp(args[0], args)
if name == "xcode-select":
    if args == ["-p"]:
        sys.exit(0 if (state / "installed").exists() else 2)
    if args[0] == "--switch":
        sys.exit(0)
if name == "brew" and args[:1] == ["doctor"]:
    if os.environ.get("CLT_TEST_DOCTOR_BROKEN"):
        sys.exit("Error: unknown check")
    if (state / "outdated").exists():
        print("Please note that these warnings are just used to help the Homebrew maintainers")
        print("with debugging if you file an issue.")
        print()
        print("Warning: Your Command Line Tools are too outdated.")
        print("Update them from Software Update in System Settings.")
        print("Warning: An unrelated finding.")
        sys.exit(1)
    print("Your system is ready to brew.")
    sys.exit(0)
if name == "softwareupdate":
    if args == ["--list"]:
        if marker.exists():
            print(os.environ.get("CLT_TEST_LIST", ""))
        sys.exit(0)
    if args[0] == "--install":
        if os.environ.get("CLT_TEST_INSTALL_FAILS"):
            sys.exit("Install failed")
        (state / "installed").touch()
        if not os.environ.get("CLT_TEST_STILL_OUTDATED"):
            (state / "outdated").unlink(missing_ok=True)
        sys.exit(0)
'''
LISTING = """Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: Command Line Tools for Xcode 9.0-9.0
\tTitle: Command Line Tools for Xcode, Version: 9.0, Size: 700000KiB, Recommended: YES,
* Label: Command Line Tools for Xcode 26.0-26.0
\tTitle: Command Line Tools for Xcode, Version: 26.0, Size: 900000KiB, Recommended: YES,
* Label: Command Line Tools for Xcode 10.2-10.2
\tTitle: Command Line Tools for Xcode, Version: 10.2, Size: 800000KiB, Recommended: YES,
"""
NEWEST = "Command Line Tools for Xcode 26.0-26.0"


class CommandLineToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="command-line-tools-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        for tool in ("grep", "sed", "sort", "tail", "touch", "rm", "printf"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        for tool in ("brew", "xcode-select", "softwareupdate", "sudo"):
            path = self.bin / tool
            path.write_text("#!" + sys.executable + "\n" + MOCK_TOOL)
            path.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.marker = self.root / "clt-marker"
        self.env = {
            "HOME": str(self.root), "PATH": str(self.bin),
            "CLT_TEST_STATE": str(self.state), "CLT_TEST_LOG": str(self.log),
            "DOTFILES_CLT_MARKER": str(self.marker), "CLT_TEST_LIST": LISTING,
        }

    def tools(self, installed=True, outdated=False):
        if installed:
            (self.state / "installed").touch()
        if outdated:
            (self.state / "outdated").touch()

    def run_update(self, **env):
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1" && update_command_line_tools', "test", str(LIBRARY)],
            env={**self.env, **env}, text=True, capture_output=True, timeout=20,
        )

    def calls(self, name=None):
        entries = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [entry for entry in entries if name is None or entry[0] == name]

    def test_current_tools_are_left_alone(self):
        self.tools()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up to date", result.stdout)
        self.assertEqual(self.calls("brew")[0][1],
                         ["doctor", "check_clt_minimum_version", "check_clt_up_to_date"])
        self.assertFalse(self.calls("softwareupdate") or self.calls("sudo"))

    def test_outdated_tools_install_the_newest_offered_label(self):
        self.tools(outdated=True)
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("too outdated", result.stdout)
        self.assertNotIn("Software Update in System Settings", result.stdout)
        self.assertIn(f"Installing {NEWEST}", result.stdout)
        self.assertEqual(self.calls("softwareupdate"), [
            ["softwareupdate", ["--list"], True],
            ["softwareupdate", ["--install", "--verbose", NEWEST], True],
        ])
        self.assertEqual(self.calls("sudo"), [["sudo", ["softwareupdate", "--install", "--verbose", NEWEST], True]])
        self.assertFalse(self.marker.exists())
        self.assertNotIn(["--switch", "/Library/Developer/CommandLineTools"],
                         [call[1] for call in self.calls("xcode-select")])
        self.assertEqual(result.stderr, "")

    def test_missing_tools_are_installed_and_selected(self):
        self.tools(installed=False)
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout)
        self.assertIn(["sudo", ["softwareupdate", "--install", "--verbose", NEWEST], True], self.calls("sudo"))
        self.assertNotIn(["--switch", "/Library/Developer/CommandLineTools"],
                         [call[1] for call in self.calls("xcode-select")])
        self.assertTrue((self.state / "installed").exists())

    def test_missing_tools_select_the_install_when_xcode_select_still_has_no_path(self):
        self.tools(installed=False)
        script = self.bin / "softwareupdate"
        script.write_text(script.read_text().replace('(state / "installed").touch()', "pass"))
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["sudo", ["xcode-select", "--switch", "/Library/Developer/CommandLineTools"], False],
                      self.calls("sudo"))

    def test_no_offered_update_prints_the_reinstall_hint_without_sudo(self):
        self.tools(outdated=True)
        result = self.run_update(CLT_TEST_LIST="No new software available.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("offers no Command Line Tools update", result.stderr)
        self.assertIn("sudo rm -rf /Library/Developer/CommandLineTools && sudo xcode-select --install",
                      result.stderr)
        self.assertFalse(self.calls("sudo"))
        self.assertFalse(self.marker.exists())

    def test_failed_or_ineffective_install_prints_the_reinstall_hint(self):
        for env in ({"CLT_TEST_INSTALL_FAILS": "1"}, {"CLT_TEST_STILL_OUTDATED": "1"}):
            with self.subTest(env=env):
                self.tools(outdated=True)
                if self.log.exists():
                    self.log.unlink()
                result = self.run_update(**env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("still missing or outdated", result.stderr)
                self.assertIn("xcode-select --install", result.stderr)
                self.assertFalse(self.marker.exists())

    def test_unrelated_doctor_failure_skips_the_update(self):
        self.tools(outdated=True)
        result = self.run_update(CLT_TEST_DOCTOR_BROKEN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not check", result.stderr)
        self.assertFalse(self.calls("softwareupdate") or self.calls("sudo"))

    def test_status_codes_for_the_audit(self):
        for setup, env, expected in (
            ({}, {}, 1), ({"outdated": True}, {}, 0), ({"installed": False}, {}, 0),
            ({"outdated": True}, {"CLT_TEST_DOCTOR_BROKEN": "1"}, 2),
        ):
            with self.subTest(setup=setup, env=env):
                for path in self.state.iterdir():
                    path.unlink()
                self.tools(**setup)
                result = subprocess.run(
                    ["/bin/bash", "-c", 'source "$1" && command_line_tools_outdated', "test", str(LIBRARY)],
                    env={**self.env, **env}, text=True, capture_output=True, timeout=20,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                if setup.get("outdated") and not env:
                    self.assertEqual(result.stdout, "Your Command Line Tools are too outdated.\n")


if __name__ == "__main__":
    unittest.main()
