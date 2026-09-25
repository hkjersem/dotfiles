"""Offline cleanup and audit-wrapper regression tests on system Bash."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MOCK_PM = r'''
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MAINTENANCE_LOG"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name + " " + " ".join(args) == os.environ.get("MAINTENANCE_FAIL"):
    sys.exit("Simulated package-manager failure")
if args == ["--version"]:
    print(os.environ.get("MAINTENANCE_PNPM_VERSION", "11.0.0"))
'''


class PackageMaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="package-maintenance-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        for pm in ("npm", "pnpm", "bun"):
            executable = self.bin / pm
            executable.write_text("#!" + sys.executable + "\n" + MOCK_PM)
            executable.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        MAINTENANCE_LOG=str(self.log))
        for key in ("BASH_ENV", "ENV", "MAINTENANCE_FAIL", "MAINTENANCE_PNPM_VERSION"):
            self.env.pop(key, None)
        self.package = self.project / "package.json"
        self.workspace = self.project / "pnpm-workspace.yaml"

    def manifest(self, pm="npm", **extra):
        self.package.write_text(json.dumps(
            {"name": "fixture", "packageManager": pm + "@1.0.0", **extra}, indent=2,
        ) + "\n")

    def run_script(self, script, *args, answers=""):
        if self.log.exists():
            self.log.unlink()
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts/package-manager" / (script + ".sh")), *args],
            cwd=self.project, env=self.env, input=answers, text=True,
            capture_output=True, timeout=20,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_empty_cleanup_plan_on_all_managers(self):
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, dependencies={"kept": "1.0.0"})
                before = self.package.read_bytes()
                result = self.run_script("clean", "--yes")
                self.assert_success(result)
                self.assertIn("Nothing to clean", result.stdout)
                self.assertEqual(self.package.read_bytes(), before)
                self.assertFalse(self.calls())

    def test_empty_sections_dry_run_refusal_and_apply(self):
        for pm in ("npm", "pnpm", "bun"):
            for newline in ("", "\n"):
                with self.subTest(pm=pm, newline=newline):
                    self.manifest(pm, dependencies={}, devDependencies={},
                                  scripts={"test": "keep"}, overrides={})
                    self.package.write_text(self.package.read_text().rstrip("\n") + newline)
                    before = self.package.read_bytes()
                    for args, answers in ((("--dry-run",), ""), ((), "n\n")):
                        result = self.run_script("clean", *args, answers=answers)
                        self.assert_success(result)
                        self.assertIn("dependencies, devDependencies, overrides", result.stdout)
                        self.assertEqual(self.package.read_bytes(), before)
                    result = self.run_script("clean", "--yes")
                    self.assert_success(result)
                    expected = {"name": "fixture", "packageManager": pm + "@1.0.0",
                                "scripts": {"test": "keep"}}
                    self.assertEqual(json.loads(self.package.read_text()), expected)
                    self.assertEqual(self.package.read_text().endswith("\n"), bool(newline))
                    self.assertFalse(self.calls())

    def test_catalog_only_cleanup_preserves_used_and_unrelated_entries(self):
        self.manifest("pnpm", dependencies={"kept": "catalog:"})
        self.workspace.write_text(
            "# preserved\ncatalog:\n  kept: '1.0.0'\n  '@scope/unused': '2.0.0'\n"
            "overrides:\n  other: '3.0.0'\n"
        )
        before = self.workspace.read_text()
        result = self.run_script("clean", "--dry-run")
        self.assert_success(result)
        self.assertEqual(self.workspace.read_text(), before)
        result = self.run_script("clean", "--yes")
        self.assert_success(result)
        self.assertEqual(self.workspace.read_text(), before.replace("  '@scope/unused': '2.0.0'\n", ""))
        self.assertEqual(json.loads(self.package.read_text())["dependencies"], {"kept": "catalog:"})

    def test_named_catalogs_are_not_rewritten(self):
        self.manifest("pnpm", devDependencies={})
        self.workspace.write_text("catalog:\n  unused: '1.0.0'\ncatalogs:\n  named:\n    pkg: '2.0.0'\n")
        before = self.workspace.read_text()
        result = self.run_script("clean", "--yes")
        self.assert_success(result)
        self.assertIn("named pnpm catalogs", result.stdout)
        self.assertEqual(self.workspace.read_text(), before)
        self.assertNotIn("devDependencies", json.loads(self.package.read_text()))

    def test_workspace_cleanup_and_suggestions_do_not_rewrite_dependencies(self):
        self.manifest("pnpm", dependencies={"shared": "^1.0.0"}, devDependencies={})
        self.workspace.write_text("packages:\n  - 'packages/*'\n")
        member = self.project / "packages/member/package.json"
        member.parent.mkdir(parents=True)
        member.write_text(json.dumps({"dependencies": {"shared": "~1.1.0"}, "optionalDependencies": {}}, indent=2))
        result = self.run_script("clean", "--yes")
        self.assert_success(result)
        self.assertIn("Catalog suggestions", result.stdout)
        self.assertEqual(json.loads(member.read_text()), {"dependencies": {"shared": "~1.1.0"}})
        self.assertEqual(json.loads(self.package.read_text())["dependencies"], {"shared": "^1.0.0"})
        self.assertNotIn("devDependencies", json.loads(self.package.read_text()))
        self.assertEqual(self.workspace.read_text(), "packages:\n  - 'packages/*'\n")

    def test_pnpm_audit_uses_version_specific_fix_flag(self):
        self.manifest("pnpm")
        for version, flag in (("10.0.0", "--fix"), ("11.0.0", "--fix=update")):
            with self.subTest(version=version):
                self.env["MAINTENANCE_PNPM_VERSION"] = version
                result = self.run_script("audit")
                self.assert_success(result)
                self.assertEqual(self.calls(), [
                    ["pnpm", ["--version"]], ["pnpm", ["audit", flag]],
                    ["pnpm", ["install", "--lockfile-only"]],
                ])

    def test_deep_audit_refreshes_workspace_lockfile_explicitly(self):
        self.manifest("pnpm")
        self.workspace.write_text("packages:\n  - 'packages/*'\n")
        result = self.run_script("audit", "--deep")
        self.assert_success(result)
        self.assertEqual(self.calls()[1], [
            "pnpm", ["-r", "--include-workspace-root", "update", "--depth", "Infinity", "--lockfile-only"],
        ])

    def test_npm_and_bun_audit_dispatch(self):
        for pm, args in (("npm", ["audit", "fix", "--package-lock-only", "--ignore-scripts"]),
                         ("bun", ["audit"])):
            with self.subTest(pm=pm):
                self.manifest(pm)
                result = self.run_script("audit")
                self.assert_success(result)
                self.assertEqual(self.calls(), [[pm, args]])

    def test_audit_failure_does_not_run_followup_install(self):
        self.manifest("pnpm")
        self.env["MAINTENANCE_FAIL"] = "pnpm audit --fix=update"
        result = self.run_script("audit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Simulated package-manager failure", result.stderr)
        self.assertFalse(any(args and args[0] == "install" for _, args in self.calls()))


if __name__ == "__main__":
    unittest.main()
