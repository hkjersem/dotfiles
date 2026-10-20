"""Offline tests for pml (scripts/package-manager/link.sh), run with /bin/bash."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/package-manager/link.sh"
MOCK_PM = """#!/usr/bin/python3
import json, os, sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MOCK_PM_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "pnpm" and args == ["--version"]:
    print(os.environ["MOCK_PNPM_VERSION"])
elif name == "npm" and args == ["root", "-g"]:
    print(os.environ["MOCK_GLOBAL_ROOT"])
"""


class PackageLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for pm in ("npm", "pnpm"):
            stub = self.bin / pm
            stub.write_text(MOCK_PM)
            stub.chmod(0o755)
        self.lib = self.tmp / "lib"
        self.lib.mkdir()
        (self.lib / "package.json").write_text(json.dumps({"name": "@scope/lib"}))
        self.app = self.tmp / "app"
        self.app.mkdir()
        self.global_root = self.tmp / "global"
        (self.global_root / "@scope").mkdir(parents=True)
        self.log = self.tmp / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "MOCK_PM_LOG": str(self.log),
            "MOCK_PNPM_VERSION": "11.1.1",
            "MOCK_GLOBAL_ROOT": str(self.global_root),
        }

    def project(self, manager=None, lockfile=None):
        manifest = {"name": "app"}
        if manager:
            manifest["packageManager"] = manager
        (self.app / "package.json").write_text(json.dumps(manifest))
        if lockfile:
            (self.app / lockfile).write_text("")

    def pml(self, *args, cwd=None):
        return subprocess.run(["/bin/bash", str(SCRIPT), *args], cwd=cwd or self.app,
                              env=self.env, capture_output=True, text=True, timeout=20)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_npm_link_and_undo_keep_declared_dependencies(self):
        self.project(lockfile="package-lock.json")
        (self.global_root / "@scope/lib").symlink_to(self.lib)

        linked = self.pml("../lib")
        undone = self.pml("--undo", "../lib")

        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(undone.returncode, 0, undone.stderr)
        # npm unlink is npm uninstall, which would remove a declared dependency.
        self.assertEqual(self.calls(), [
            ["npm", "link", str(self.lib)],
            ["npm", "install"],
            ["npm", "root", "-g"],
            ["npm", "rm", "-g", "@scope/lib"],
        ])

    def test_npm_undo_keeps_a_global_package_from_elsewhere(self):
        self.project(lockfile="package-lock.json")
        other = self.tmp / "other"
        other.mkdir()
        (self.global_root / "@scope/lib").symlink_to(other)

        result = self.pml("--undo", "../lib")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(["npm", "rm", "-g", "@scope/lib"], self.calls())

    def test_pnpm_links_by_path_and_unlinks_by_name(self):
        (self.tmp / "package.json").write_text(json.dumps({"packageManager": "pnpm@11.1.1"}))

        linked = self.pml("lib", cwd=self.tmp)
        undone = self.pml("--undo", "lib", cwd=self.tmp)

        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(undone.returncode, 0, undone.stderr)
        # A bare word would be read as a package name, and pnpm 12 resolves a
        # relative path from the project root rather than the current folder.
        self.assertEqual([c for c in self.calls() if c[1] != "--version"], [
            ["pnpm", "link", str(self.lib)],
            ["pnpm", "unlink", "@scope/lib"],
        ])

    def test_pnpm_undo_removes_the_link_dependency_older_pnpm_keeps(self):
        # pnpm fixed unlink in 11.28.0 and 12.7.0; before that it kept the
        # link: dependency that pnpm link added to package.json.
        for version, removes in (("11.1.1", True), ("12.6.9", True), ("11.28.0", False)):
            with self.subTest(version=version):
                self.log.unlink(missing_ok=True)
                self.env["MOCK_PNPM_VERSION"] = version
                (self.app / "package.json").write_text(json.dumps({
                    "packageManager": f"pnpm@{version}",
                    "dependencies": {"@scope/lib": "link:../lib"}}))
                (self.app / "pnpm-workspace.yaml").write_text("{}\n")
                sub = self.app / "src"
                sub.mkdir(exist_ok=True)

                result = self.pml("--undo", "../../lib", cwd=sub)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(["pnpm", "remove", "@scope/lib"] in self.calls(), removes)
                self.assertEqual((self.app / "pnpm-workspace.yaml").exists(), not removes)

    def test_pnpm_undo_keeps_a_link_to_another_directory(self):
        other = self.tmp / "other"
        other.mkdir()
        (self.app / "package.json").write_text(json.dumps({
            "packageManager": "pnpm@11.1.1",
            "dependencies": {"@scope/lib": "link:../other"}}))

        result = self.pml("--undo", "../lib")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(["pnpm", "remove", "@scope/lib"], self.calls())

    def test_pnpm_undo_keeps_a_workspace_file_with_content(self):
        self.project(manager="pnpm@11.1.1")
        workspace = self.app / "pnpm-workspace.yaml"
        workspace.write_text("packages:\n  - lib\n")

        result = self.pml("--undo", "../lib")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(workspace.exists())

    def test_pnpm_10_is_refused(self):
        self.project(manager="pnpm@10.29.3")
        self.env["MOCK_PNPM_VERSION"] = "10.29.3"

        result = self.pml("../lib")

        self.assertEqual(result.returncode, 1)
        self.assertIn("pml needs pnpm 11 or newer (found 10.29.3)", result.stderr)
        self.assertEqual(self.calls(), [["pnpm", "--version"]])

    def test_bun_and_yarn_projects_are_refused(self):
        for manager in ("bun@1.4.3", "yarn@4.9.2"):
            with self.subTest(manager=manager):
                self.project(manager=manager)

                result = self.pml("../lib")

                self.assertEqual(result.returncode, 1)
                self.assertIn(f"pml supports npm and pnpm, not {manager.split('@')[0]}",
                              result.stderr)
                self.assertEqual(self.calls(), [])

    def test_library_without_a_name_is_refused(self):
        self.project(lockfile="package-lock.json")
        (self.lib / "package.json").write_text("{}")

        result = self.pml("../lib")

        self.assertEqual(result.returncode, 1)
        self.assertIn("has no name", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
