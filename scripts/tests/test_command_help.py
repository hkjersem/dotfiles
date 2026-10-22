"""Tests for dotfiles shell commands run through bash/aliases."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
ALIASES = REPO / "bash" / "aliases"
# Tools a command must not run unless the test expects it.
TOOLS = ("bun", "corepack", "fnm", "git", "node", "npm", "npx", "pnpm")


class Sandbox:
    """An isolated HOME linking this repository, a project folder, and failing stand-ins for TOOLS."""

    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".dotfiles").symlink_to(REPO, target_is_directory=True)
        (self.home / ".aliases").symlink_to(ALIASES)
        self.project = self.root / "project"
        (self.project / "node_modules" / "left").mkdir(parents=True)
        (self.project / "notes.txt").write_text("kept\n")
        self.calls = self.root / "tool-calls"
        tools = self.root / "bin"
        tools.mkdir()
        for tool in TOOLS:
            path = tools / tool
            path.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{self.calls}"\nexit 1\n')
            path.chmod(0o755)
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(tools), str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
            "TERM": "xterm",
            "LANG": "en_US.UTF-8",
        }

    def files(self):
        return sorted(str(p.relative_to(self.project)) for p in self.project.rglob("*"))

    def tool_calls(self):
        return self.calls.read_text() if self.calls.exists() else ""


class WipeTests(unittest.TestCase):
    def run_wipe(self, sandbox, *args):
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$HOME/.aliases"\nwipe "$@"', "wipe", *args],
            cwd=sandbox.project, env=sandbox.env, capture_output=True, text=True, timeout=30,
        )

    def test_wipe_shows_help_or_rejects_unknown_options_without_deleting(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            (sandbox.project / "build").mkdir()
            before = sandbox.files()
            for args in (("build", "--help"), ("build", "-h"), ("--dir", "build", "-h")):
                with self.subTest(args=args):
                    result = self.run_wipe(sandbox, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertRegex(result.stdout, r"^Usage: wipe ")
                    self.assertEqual(sandbox.files(), before)
            for args in (("build", "--dirs"), ("--dir", "build"), ("build", "--dir", "extra")):
                with self.subTest(args=args):
                    result = self.run_wipe(sandbox, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Usage: wipe", result.stderr)
                    self.assertEqual(sandbox.files(), before)

    def test_wipe_deletes_matching_folders_outside_node_modules(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            (sandbox.project / "build").mkdir()
            (sandbox.project / "app" / "build").mkdir(parents=True)
            (sandbox.project / "node_modules" / "build").mkdir()
            result = self.run_wipe(sandbox, "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sandbox.files(), [
                "app", "node_modules", "node_modules/build", "node_modules/left", "notes.txt"])


if __name__ == "__main__":
    unittest.main()
