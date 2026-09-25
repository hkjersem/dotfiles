"""Isolated regression tests for directory-based Git identity setup."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
BEGIN = "# --- BEGIN setup-git-local.sh ---"
END = "# --- END setup-git-local.sh ---"


class GitLocalSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="git-identity-test-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "home with spaces"
        self.home.mkdir()
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("GIT_") or key in ("BASH_ENV", "ENV"):
                self.env.pop(key)
        self.env.update(
            HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"),
        )
        self.git("config", "--global", "user.name", "Personal # Example")
        self.git("config", "--global", "user.email", "personal@example.invalid")
        self.git("config", "--global", "include.path", "~/.gitconfig_local")
        alias = self.git("config", "--file", str(REPO / "git/gitconfig"), "alias.whoami")
        self.git("config", "--global", "alias.whoami", alias.strip())
        self.output = self.home / ".gitconfig_local"
        self.work = self.home / "Developer"
        self.project = self.work / "project"
        self.exception = self.work / "personal"
        for directory in (self.project, self.exception):
            self.git("init", "-q", str(directory))

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], env=self.env, cwd=self.home, text=True,
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def run_setup(self, answers):
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts/setup-git-local.sh")],
            input=answers, env=self.env, cwd=self.home, text=True,
            capture_output=True, timeout=10,
        )

    def identity(self, directory, key="email"):
        return self.git("-C", str(directory), "config", "user." + key).strip()

    def configure_work(self):
        result = self.run_setup(
            'y\n~/Developer/\nwork@example.invalid\nWork # "Example"\n'
            '~/Developer/personal/\n\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.home.glob(".gitconfig*")
                if path.is_file()}

    def test_work_identity_and_personal_exception(self):
        self.configure_work()
        self.assertEqual(self.identity(self.project), "work@example.invalid")
        self.assertEqual(self.identity(self.project, "name"), 'Work # "Example"')
        self.assertEqual(self.identity(self.exception), "personal@example.invalid")
        self.assertEqual(self.identity(self.exception, "name"), "Personal # Example")
        for name in (".gitconfig_local", ".gitconfig_local_work", ".gitconfig_local_personal"):
            self.assertEqual((self.home / name).stat().st_mode & 0o777, 0o600)

    def test_whoami_reports_effective_identity_and_source(self):
        self.configure_work()
        result = subprocess.run(
            ["git", "-C", str(self.project), "whoami"], env=self.env,
            text=True, capture_output=True, timeout=10,
        )
        if result.returncode != 0 and "BLOCKED by sandbox: 'git whoami'" in result.stderr:
            self.skipTest("The sandbox Git guard does not allow custom aliases.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Work # "Example" <work@example.invalid>', result.stdout)
        self.assertIn("~/.gitconfig_local_work", result.stdout)

    def test_personal_setup_needs_no_work_identity(self):
        result = self.run_setup("\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.identity(self.project), "personal@example.invalid")
        self.assertFalse((self.home / ".gitconfig_local_work").exists())

    def test_reconfigure_preserves_unmanaged_settings(self):
        self.configure_work()
        prefix = "[core]\n\teditor = example-editor\n"
        suffix = "\n[fetch]\n\tprune = true\n"
        self.output.write_text(prefix + self.output.read_text() + suffix)
        result = self.run_setup("y\ny\n\nchanged@example.invalid\n\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.read_text().startswith(prefix))
        self.assertTrue(self.output.read_text().endswith(suffix))
        self.assertEqual(self.output.read_text().count(BEGIN), 1)
        self.assertEqual(self.identity(self.project), "changed@example.invalid")
        self.assertEqual(self.identity(self.exception), "changed@example.invalid")
        self.assertEqual(self.identity(self.project, "name"), "Personal # Example")

    def test_appends_to_unmanaged_config(self):
        original = "[fetch]\n\tprune = true\n"
        self.output.write_text(original)
        result = self.run_setup("y\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.read_text().startswith(original))
        self.assertIn(BEGIN, self.output.read_text())

    def test_declining_reconfiguration_changes_nothing(self):
        self.configure_work()
        before = self.snapshot()
        result = self.run_setup("\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_removing_managed_work_overrides_keeps_identity_files(self):
        self.configure_work()
        result = self.run_setup("y\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.identity(self.project), "personal@example.invalid")
        self.assertTrue((self.home / ".gitconfig_local_work").exists())

    def test_cancelled_input_never_changes_config(self):
        before = self.snapshot()
        for answers in ("", "y\n", "y\n\n", "y\n\nwork@example.invalid\n"):
            with self.subTest(answers=answers):
                result = self.run_setup(answers)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Input cancelled", result.stderr)
                self.assertEqual(self.snapshot(), before)
        self.configure_work()
        before = self.snapshot()
        for answers in ("", "y\n", "y\ny\n\nwork@example.invalid\nName\n"):
            with self.subTest(answers=answers):
                result = self.run_setup(answers)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot(), before)

    def test_empty_work_email_is_rejected_before_writes(self):
        before = self.snapshot()
        result = self.run_setup("y\n\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Work email must not be empty", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_managed_markers_leave_config_unchanged(self):
        for content in (BEGIN + "\n", END + "\n", f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n"):
            with self.subTest(content=content):
                self.output.write_text(content)
                before = self.snapshot()
                result = self.run_setup("y\nn\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("managed block", result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(list(self.home.glob(".gitconfig-setup.*")))

    def test_invalid_existing_config_is_not_replaced(self):
        self.output.write_text("[invalid\n")
        before = self.snapshot()
        result = self.run_setup("y\nn\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_directory_output_is_reported_as_error(self):
        (self.home / ".gitconfig_local_work").mkdir()
        result = self.run_setup("y\n\nwork@example.invalid\n\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)
        self.assertFalse(self.output.exists())

    def test_write_failure_does_not_report_success(self):
        before = self.snapshot()
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        mock = bin_dir / "mv"
        mock.write_text("#!/bin/bash\nprintf 'Simulated write failure\\n' >&2\nexit 1\n")
        mock.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        result = self.run_setup("n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not save", result.stderr)
        self.assertNotIn("Updated ", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.home.glob(".gitconfig-setup.*")))

    def test_relative_work_directory_is_rejected(self):
        before = self.snapshot()
        result = self.run_setup("y\nDeveloper\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute directory", result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
