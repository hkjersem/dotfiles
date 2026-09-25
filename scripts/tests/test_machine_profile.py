"""Offline tests for machine profiles, tool updates, and setup/update/audit."""

import json
import os
from pathlib import Path
import pty
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
with open(os.environ["PROFILE_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, args, os.environ.get("DOTFILES_PROFILE")]) + "\n")
if name + " " + " ".join(args) == os.environ.get("PROFILE_TEST_FAIL"):
    sys.exit("mock failure")
root = Path(os.environ["PROFILE_TEST_ROOT"])
nav = root / "bin/nav-pilot"
formulae = ["bat", "coreutils", "fnm", "fzf", "git-filter-repo", "jq", "mas",
            "ripgrep", "tree"]
if name == "brew":
    if args == ["--cellar"]:
        print(root / "Cellar")
    elif args == ["--prefix"]:
        print(root)
    elif args[:2] == ["bundle", "install"]:
        if os.environ.get("DOTFILES_PROFILE") == "work" and not nav.exists():
            target = root / "Cellar/nav-pilot/1.0/bin/nav-pilot"
            target.parent.mkdir(parents=True)
            target.write_text(Path(sys.argv[0]).read_text())
            target.chmod(0o755)
            nav.symlink_to(target)
        if os.environ.get("DOTFILES_PROFILE") == "work" and not (root / "bin/gcloud").exists():
            target = root / "share/google-cloud-sdk/bin/gcloud"
            target.parent.mkdir(parents=True)
            target.write_text(Path(sys.argv[0]).read_text())
            target.chmod(0o755)
            (root / "bin/gcloud").symlink_to(target)
    elif args[:2] == ["bundle", "list"]:
        print("\n".join(formulae))
        if os.environ.get("DOTFILES_PROFILE") == "work":
            print("navikt/tap/nav-pilot")
    elif args[:1] == ["list"]:
        print("\n".join(formulae + (["nav-pilot"] if nav.exists() else [])))
    elif args == ["leaves"]:
        print("\n".join(formulae + (["nav-pilot"] if nav.exists() else [])))
elif name == "npm" and args[:2] == ["list", "-g"]:
    print("corepack@1.0.0")
'''


class MachineProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="machine-profile-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.dotfiles = self.home / ".dotfiles"
        self.dotfiles.mkdir()
        for relative in (
            "Brewfile", "install.conf.yaml", "scripts/audit.sh",
            "scripts/machine-profile.sh", "scripts/lib/tool-updates.sh",
            "macos/install.sh", "macos/applications.sh", "macos/update.sh",
            "zsh/conf.d/omz.zsh",
        ):
            target = self.dotfiles / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        (self.dotfiles / "scripts/audit-skills.sh").write_text("# unrelated to profile tests\n")
        self.profile = self.home / ".config/dotfiles/profile"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Isolate command discovery from the real machine's optional tools.
        for tool in ("cat", "mkdir", "mktemp", "mv", "rm", "realpath", "dirname",
                     "basename", "grep", "sed", "awk", "tr", "ls", "readlink", "sort",
                     "uname"):
            self.bin.joinpath(tool).symlink_to(shutil.which(tool))
        self.mock = "#!" + sys.executable + "\n" + MOCK_TOOL
        for tool in ("brew", "npm", "fnm", "pnpm", "bash", "zsh", "softwareupdate"):
            self.write_mock(self.bin / tool)
        # The updater's cleanup must not delete even isolated fixture data.
        (self.bin / "rm").unlink()
        self.write_mock(self.bin / "rm")
        self.log = self.root / "calls.jsonl"
        self.env = dict(os.environ)
        for key in ("BASH_ENV", "ENV", "DOTFILES_PROFILE"):
            self.env.pop(key, None)
        self.env.update(HOME=str(self.home), PATH=str(self.bin),
                        PROFILE_TEST_ROOT=str(self.root),
                        PROFILE_TEST_LOG=str(self.log))

    def write_mock(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.mock)
        path.chmod(0o755)

    def run_script(self, relative, *args, env=None):
        return subprocess.run(
            ["/bin/bash", str(self.dotfiles / relative), *args],
            env=env or self.env, cwd=self.root, input="", text=True,
            capture_output=True, timeout=20,
        )

    def set_profile(self, value):
        self.profile.parent.mkdir(parents=True, exist_ok=True)
        self.profile.write_text(value + "\n")

    def run_tool_update(self, tool, env=None):
        return subprocess.run(
            ["/bin/bash", "-c",
             'source "$1/scripts/machine-profile.sh" && ensure_machine_profile '
             '&& source "$1/scripts/lib/tool-updates.sh" && update_installed_tool "$2"',
             "test", str(self.dotfiles), tool],
            env=env or self.env, cwd=self.root, input="", text=True,
            capture_output=True, timeout=20,
        )

    def calls(self, tool=None):
        entries = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [entry for entry in entries if tool is None or entry[0] == tool]

    def install_nav(self, brew_managed=False):
        if brew_managed:
            target = self.root / "Cellar/nav-pilot/1.0/bin/nav-pilot"
            self.write_mock(target)
            (self.bin / "nav-pilot").symlink_to(target)
        else:
            self.write_mock(self.bin / "nav-pilot")

    def declared_tools(self, profile):
        result = subprocess.run(
            ["/usr/bin/ruby", "--disable-gems", "-e",
             "def brew(name); puts name; end; def cask(name); puts name; end; load ARGV[0]",
             str(self.dotfiles / "Brewfile")],
            env=dict(self.env, DOTFILES_PROFILE=profile), text=True,
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_profile_set_show_and_change(self):
        for value in ("personal", "work", "personal"):
            result = self.run_script("scripts/machine-profile.sh", value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.profile.read_text(), value + "\n")
            self.assertEqual(self.profile.stat().st_mode & 0o777, 0o600)
            result = self.run_script("scripts/machine-profile.sh")
            self.assertEqual(result.stdout.strip(), "Machine profile: " + value)
        self.assertFalse(self.calls("brew"))
        self.assertFalse(self.calls("nav-pilot"))

    def test_noninteractive_missing_profile_fails_before_side_effects(self):
        for script in ("scripts/machine-profile.sh", "macos/install.sh",
                       "macos/applications.sh", "macos/update.sh"):
            with self.subTest(script=script):
                result = self.run_script(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("set a machine profile first", result.stderr)
                self.assertFalse(self.profile.exists())
                self.assertFalse(self.calls())

    def test_invalid_inputs_do_not_replace_saved_profile(self):
        self.set_profile("work")
        for args in (("other",), ("work", "personal")):
            result = self.run_script("scripts/machine-profile.sh", *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.profile.read_text(), "work\n")

    def test_invalid_saved_profile_is_not_executed_or_defaulted(self):
        for value in ("", "work personal", "work\npersonal",
                      '$(touch "$HOME/executed")'):
            with self.subTest(value=value):
                self.set_profile(value)
                result = self.run_script("macos/update.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid machine profile", result.stderr)
                self.assertFalse(self.home.joinpath("executed").exists())
                self.assertFalse(self.calls())

    def test_profile_write_error_is_explicit(self):
        self.home.joinpath(".config").write_text("not a directory")
        result = self.run_script("scripts/machine-profile.sh", "work")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not save", result.stderr)

    def test_first_interactive_run_retries_and_persists_choice(self):
        try:
            master, slave = pty.openpty()
        except OSError as error:
            self.skipTest(f"Pseudo-terminal unavailable: {error}")
        try:
            process = subprocess.Popen(
                ["/bin/bash", str(self.dotfiles / "scripts/machine-profile.sh")],
                env=self.env, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            os.write(master, b"invalid\npersonal\n")
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("Is this a personal or work machine? [personal/work]", stderr)
            self.assertIn("Please enter personal or work", stderr)
            self.assertIn("Machine profile: personal", stdout)
            self.assertEqual(self.profile.read_text(), "personal\n")
            self.assertEqual(self.run_script("scripts/machine-profile.sh").returncode, 0)
        finally:
            os.close(master)
            os.close(slave)

    def test_brewfile_only_declares_work_tools_for_work(self):
        for profile in ("work", "personal", ""):
            declared = self.declared_tools(profile)
            self.assertEqual("navikt/tap/nav-pilot" in declared, profile == "work")
            self.assertEqual("gcloud-cli" in declared, profile == "work")
            self.assertIn("fnm", declared)

    def test_brewfile_does_not_duplicate_existing_gcloud(self):
        self.write_mock(self.bin / "gcloud")
        self.assertNotIn("gcloud-cli", self.declared_tools("work"))
        self.assertFalse(self.calls("gcloud"))

    def test_native_gcloud_updates_on_both_profiles(self):
        self.write_mock(self.bin / "gcloud")
        for profile in ("personal", "work"):
            self.set_profile(profile)
            if self.log.exists():
                self.log.unlink()
            result = self.run_tool_update("gcloud")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([call[1] for call in self.calls("gcloud")],
                             [["components", "update", "--quiet"]])

    def test_personal_gcloud_is_updated_by_full_updater(self):
        self.set_profile("personal")
        self.write_mock(self.bin / "gcloud")
        result = self.run_script("macos/update.sh", "--no-defaults")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call[1] for call in self.calls("gcloud")],
                         [["components", "update", "--quiet"]])
        self.assertEqual(result.stdout.count("Tools & integrations"), 1)

    def test_personal_software_heading_is_shared_by_multiple_updates(self):
        self.set_profile("personal")
        self.install_nav()
        self.write_mock(self.bin / "gcloud")
        result = self.run_script("macos/update.sh", "--no-defaults")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("Tools & integrations"), 1)
        self.assertTrue(self.calls("nav-pilot"))
        self.assertTrue(self.calls("gcloud"))

    def test_brew_gcloud_uses_greedy_cask_upgrade_on_either_profile(self):
        for location in ("share/google-cloud-sdk/bin/gcloud",
                         "Caskroom/gcloud-cli/1.0/bin/gcloud",
                         "Caskroom/google-cloud-sdk/1.0/bin/gcloud"):
            target = self.root / location
            self.write_mock(target)
            link = self.bin / "gcloud"
            if link.is_symlink():
                link.unlink()
            link.symlink_to(target)
            for profile in ("personal", "work"):
                with self.subTest(location=location, profile=profile):
                    self.set_profile(profile)
                    if self.log.exists():
                        self.log.unlink()
                    result = self.run_tool_update("gcloud")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(["brew", ["upgrade", "--cask", "--greedy", "gcloud-cli"],
                                   profile], self.calls())
                    self.assertFalse(self.calls("gcloud"))

    def test_missing_gcloud_skips_personal_but_errors_for_work(self):
        for profile in ("personal", "work"):
            self.set_profile(profile)
            result = self.run_tool_update("gcloud")
            self.assertEqual(result.returncode, 0 if profile == "personal" else 1)
            if profile == "work":
                self.assertIn("gcloud is missing", result.stderr)
            self.assertFalse(self.calls())

    def test_gcloud_failures_are_reported(self):
        self.set_profile("personal")
        self.write_mock(self.bin / "gcloud")
        result = self.run_tool_update("gcloud", env=dict(
            self.env, PROFILE_TEST_FAIL="gcloud components update --quiet"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error: gcloud component update failed", result.stderr)
        (self.bin / "gcloud").unlink()
        target = self.root / "share/google-cloud-sdk/bin/gcloud"
        self.write_mock(target)
        (self.bin / "gcloud").symlink_to(target)
        result = self.run_tool_update("gcloud", env=dict(
            self.env, PROFILE_TEST_FAIL="brew upgrade --cask --greedy gcloud-cli"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error: Homebrew gcloud upgrade failed", result.stderr)

    def test_updater_installs_only_on_work_profile(self):
        for profile in ("personal", "work"):
            with self.subTest(profile=profile):
                self.set_profile(profile)
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script("macos/update.sh", "--no-defaults",
                                         env=dict(self.env, DOTFILES_PROFILE="wrong"))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(all(call[2] == profile for call in self.calls("brew")))
                self.assertEqual((self.bin / "nav-pilot").exists(), profile == "work")
                self.assertEqual((self.bin / "gcloud").exists(), profile == "work")
                self.assertEqual([call[1] for call in self.calls("nav-pilot")],
                                 [["sync", "--apply"]] if profile == "work" else [])
                self.assertEqual(result.stdout.count("Tools & integrations"), 1)

    def test_missing_optional_clis_skip_on_both_profiles(self):
        for profile in ("personal", "work"):
            self.set_profile(profile)
            for tool in ("claude", "copilot", "codex"):
                with self.subTest(profile=profile, tool=tool):
                    result = self.run_tool_update(tool)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(self.calls())

    def test_native_clis_update_on_both_profiles(self):
        tools = ("claude", "copilot", "codex")
        for tool in tools:
            self.write_mock(self.bin / tool)
        for profile in ("personal", "work"):
            with self.subTest(profile=profile):
                self.set_profile(profile)
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script("macos/update.sh", "--no-defaults")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for tool in tools:
                    self.assertEqual([call[1] for call in self.calls(tool)], [["update"]])
                for label in ("Claude Code", "GitHub Copilot CLI", "Codex"):
                    self.assertIn(label, result.stdout)

    def test_brew_clis_skip_native_update_except_copilot(self):
        for profile in ("personal", "work"):
            self.set_profile(profile)
            for location in ("Cellar", "Caskroom"):
                for tool in ("claude", "copilot", "codex"):
                    with self.subTest(profile=profile, location=location, tool=tool):
                        target = self.root / location / tool / "1.0/bin" / tool
                        self.write_mock(target)
                        executable = self.bin / tool
                        if executable.is_symlink():
                            executable.unlink()
                        executable.symlink_to(target)
                        if self.log.exists():
                            self.log.unlink()
                        result = self.run_tool_update(tool)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        expected = [["update"]] if tool == "copilot" else []
                        self.assertEqual([call[1] for call in self.calls(tool)], expected)
                        if tool != "copilot":
                            self.assertEqual(result.stdout, "")

    def test_cli_update_failure_stops_before_success_message(self):
        self.set_profile("personal")
        tools = ("claude", "copilot", "codex")
        for tool in tools:
            self.write_mock(self.bin / tool)
        for index, tool in enumerate(tools):
            with self.subTest(tool=tool):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script(
                    "macos/update.sh", "--no-defaults",
                    env=dict(self.env, PROFILE_TEST_FAIL=tool + " update"),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("update failed", result.stderr)
                self.assertNotIn("Done. Enjoy", result.stdout)
                for later in tools[index + 1:]:
                    self.assertFalse(self.calls(later))

    def test_existing_native_nav_upgrades_and_syncs_on_both_profiles(self):
        self.install_nav()
        for profile in ("personal", "work"):
            self.set_profile(profile)
            if self.log.exists():
                self.log.unlink()
            result = self.run_tool_update("nav-pilot")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([call[1] for call in self.calls("nav-pilot")],
                             [["upgrade"], ["sync", "--apply"]])

    def test_personal_brew_nav_updates_via_brew_then_syncs(self):
        self.set_profile("personal")
        self.install_nav(brew_managed=True)
        result = self.run_script("macos/update.sh", "--no-defaults")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertIn(["brew", ["upgrade", "--yes"], "personal"], calls)
        self.assertEqual([call[1] for call in self.calls("nav-pilot")], [["sync", "--apply"]])
        self.assertLess(calls.index(["brew", ["upgrade", "--yes"], "personal"]),
                        calls.index(["nav-pilot", ["sync", "--apply"], "personal"]))

    def test_missing_work_nav_is_not_silently_ignored(self):
        self.set_profile("work")
        result = self.run_tool_update("nav-pilot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nav Pilot is missing", result.stderr)

    def test_missing_personal_nav_is_not_installed(self):
        self.set_profile("personal")
        result = self.run_tool_update("nav-pilot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls())

    def test_brew_failure_stops_updater_before_sync(self):
        self.set_profile("work")
        self.install_nav(brew_managed=True)
        for failure in (
            f"brew bundle install --file={self.dotfiles}/Brewfile --quiet",
            "brew upgrade --yes",
        ):
            with self.subTest(failure=failure):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script("macos/update.sh", "--no-defaults",
                                         env=dict(self.env, PROFILE_TEST_FAIL=failure))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mock failure", result.stderr)
                self.assertFalse(self.calls("nav-pilot"))

    def test_upgrade_and_sync_failures_are_reported(self):
        self.set_profile("personal")
        self.install_nav()
        for failure in ("nav-pilot upgrade", "nav-pilot sync --apply"):
            if self.log.exists():
                self.log.unlink()
            result = self.run_tool_update("nav-pilot",
                                          env=dict(self.env, PROFILE_TEST_FAIL=failure))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Error: Nav Pilot", result.stderr)
            if failure.endswith("upgrade"):
                self.assertEqual([call[1] for call in self.calls("nav-pilot")], [["upgrade"]])

    def test_audit_is_read_only_and_personal_note_is_not_a_warning(self):
        missing = self.run_script("scripts/audit.sh")
        self.assertIn("Machine profile is not set", missing.stdout)
        self.assertFalse(self.profile.exists())
        self.set_profile("personal")
        self.install_nav(brew_managed=True)
        self.write_mock(self.bin / "gcloud")
        result = self.run_script("scripts/audit.sh")
        section = result.stdout.split("Machine profile", 1)[1].split("Symlinks", 1)[0]
        self.assertIn("Nav Pilot is installed on this personal machine", section)
        self.assertIn("gcloud is installed on this personal machine", section)
        self.assertNotIn("🟡", section)
        self.assertNotIn("🔴", section)
        self.assertNotIn("nav-pilot (installed but not", result.stdout)
        self.assertFalse(self.calls("nav-pilot"))
        self.assertFalse(self.calls("gcloud"))
        self.assertEqual(self.profile.read_text(), "personal\n")

    def test_audit_only_shows_profile_when_expected_tools_are_present(self):
        for profile in ("personal", "work"):
            with self.subTest(profile=profile):
                self.set_profile(profile)
                if profile == "work":
                    self.install_nav(brew_managed=True)
                    self.write_mock(self.bin / "gcloud")
                result = self.run_script("scripts/audit.sh")
                section = result.stdout.split("Machine profile", 1)[1].split("Symlinks", 1)[0]
                lines = [line for line in section.splitlines() if line.startswith("  ")]
                self.assertEqual(lines, ["  Profile: " + profile])
                self.assertFalse(self.calls("nav-pilot"))
                self.assertFalse(self.calls("gcloud"))

    def test_audit_warns_when_work_nav_is_missing(self):
        self.set_profile("work")
        result = self.run_script("scripts/audit.sh")
        section = result.stdout.split("Machine profile", 1)[1].split("Symlinks", 1)[0]
        self.assertIn("Nav Pilot is missing", section)
        self.assertIn("gcloud is missing", section)
        self.assertIn("🟡", section)
        self.assertNotIn("🔴", section)
        self.assertFalse((self.bin / "nav-pilot").exists())

    def test_audit_reports_invalid_profile_without_replacing_it(self):
        self.set_profile("invalid")
        result = self.run_script("scripts/audit.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Machine profile could not be loaded", result.stdout)
        self.assertEqual(self.profile.read_text(), "invalid\n")


if __name__ == "__main__":
    unittest.main()
