"""Isolated tests for cooldown configuration and plugin maintenance."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MOCK_TOOL = r'''
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["SETUP_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name + " " + " ".join(args) == os.environ.get("SETUP_TEST_FAIL"):
    sys.exit("Simulated maintenance failure")
if name == "pnpm" and args[:2] == ["config", "get"]:
    path = Path(os.environ["SETUP_TEST_STATE"])
    print(path.read_text() if path.exists() else "undefined")
elif name == "pnpm" and args[:2] == ["config", "set"]:
    Path(os.environ["SETUP_TEST_STATE"]).write_text(args[3])
elif name == "git" and args[:1] == ["clone"]:
    Path(args[-1]).mkdir(parents=True)
'''


class MaintenanceSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="maintenance-setup-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in ("grep", "sed", "awk", "head", "tr", "xargs", "dirname", "mv"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.log = self.root / "calls.jsonl"
        self.state = self.root / "pnpm-setting"
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        SETUP_TEST_LOG=str(self.log), SETUP_TEST_STATE=str(self.state))
        for key in ("BASH_ENV", "ENV", "SETUP_TEST_FAIL"):
            self.env.pop(key, None)
        self.npmrc = self.home / ".npmrc"
        self.bunfig = self.home / ".bunfig.toml"

    def install_mock(self, name):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + MOCK_TOOL)
        path.chmod(0o755)

    def run_script(self, name):
        if self.log.exists():
            self.log.unlink()
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts" / name)],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_only_npm_is_configured_when_optional_managers_are_absent(self):
        result = self.run_script("ensure-pm-config.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.npmrc.read_text(), "min-release-age=3\n")
        self.assertFalse(self.bunfig.exists())
        self.assertFalse(self.calls())

    def test_existing_cooldowns_are_idempotent(self):
        self.install_mock("pnpm")
        self.install_mock("bun")
        self.npmrc.write_text("registry=https://registry.example.invalid\nmin-release-age=3\n")
        self.bunfig.write_text('[install]\nminimumReleaseAge = 259200\ncache = true\n')
        self.state.write_text("4320")
        before = (self.npmrc.read_bytes(), self.bunfig.read_bytes())
        for _ in range(2):
            result = self.run_script("ensure-pm-config.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.npmrc.read_bytes(), self.bunfig.read_bytes()), before)
            self.assertEqual(self.calls(), [["pnpm", ["config", "get", "minimumReleaseAge", "--global"]]])

    def test_npm_setting_does_not_match_comments_or_longer_values(self):
        for content in ("# min-release-age=3\n", "min-release-age=30\n"):
            with self.subTest(content=content):
                self.npmrc.write_text(content)
                result = self.run_script("ensure-pm-config.sh")
                self.assertEqual(result.returncode, 0, result.stderr)
                active = [line for line in self.npmrc.read_text().splitlines()
                          if line.startswith("min-release-age=")]
                self.assertEqual(active, ["min-release-age=3"])

    def test_changed_settings_preserve_unrelated_configuration(self):
        self.install_mock("pnpm")
        self.install_mock("bun")
        self.npmrc.write_text("registry=https://registry.example.invalid\nmin-release-age=1\n")
        self.bunfig.write_text('[install]\nminimumReleaseAge = 60\ntrustedDependencies = ["keep"]\n'
                               '[other]\nminimumReleaseAge = 17\n')
        result = self.run_script("ensure-pm-config.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "4320")
        self.assertIn("registry=https://registry.example.invalid", self.npmrc.read_text())
        self.assertEqual(self.bunfig.read_text(),
                         '[install]\nminimumReleaseAge = 259200\ntrustedDependencies = ["keep"]\n'
                         '[other]\nminimumReleaseAge = 17\n')

    def test_bun_setting_can_be_created_inserted_or_appended(self):
        self.install_mock("bun")
        for content in (None, "[install]", '[other]\nvalue = "keep"\n'):
            with self.subTest(content=content):
                if self.bunfig.exists():
                    self.bunfig.unlink()
                if content is not None:
                    self.bunfig.write_text(content)
                result = self.run_script("ensure-pm-config.sh")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("[install]\nminimumReleaseAge = 259200\n", self.bunfig.read_text())
                if content and content.startswith("[other]"):
                    self.assertTrue(self.bunfig.read_text().startswith(content))

    def test_pnpm_failure_stops_before_success_or_bun_changes(self):
        self.install_mock("pnpm")
        self.install_mock("bun")
        for command in ("get minimumReleaseAge", "set minimumReleaseAge 4320"):
            with self.subTest(command=command):
                self.env["SETUP_TEST_FAIL"] = "pnpm config " + command + " --global"
                result = self.run_script("ensure-pm-config.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Simulated maintenance failure", result.stderr)
                self.assertNotIn("pnpm: set", result.stdout)
                self.assertFalse(self.bunfig.exists())

    def test_plugins_clone_when_missing_and_pull_when_present(self):
        self.install_mock("git")
        result = self.run_script("install-zsh-plugins.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 4)
        self.assertTrue(all(args[0] == "clone" for _, args in self.calls()))
        result = self.run_script("install-zsh-plugins.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 3)
        self.assertTrue(all(args[0] == "-C" and args[-1] == "pull" for _, args in self.calls()))

    def test_plugin_failure_stops_before_later_clones(self):
        self.install_mock("git")
        omz = self.home / ".oh-my-zsh"
        plugin = omz / "custom/plugins/zsh-syntax-highlighting"
        for command in (
            "git clone https://github.com/robbyrussell/oh-my-zsh.git " + str(omz),
            "git clone https://github.com/zsh-users/zsh-syntax-highlighting.git " + str(plugin),
            "git -C " + str(plugin) + " pull",
        ):
            with self.subTest(command=command):
                self.env["SETUP_TEST_FAIL"] = command
                result = self.run_script("install-zsh-plugins.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Simulated maintenance failure", result.stderr)
                self.assertEqual(len(self.calls()), 1)
                if not omz.exists():
                    omz.mkdir()
                else:
                    plugin.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    unittest.main()
