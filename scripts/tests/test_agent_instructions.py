"""Global agent instruction linking tests using disposable homes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class AgentInstructionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agent-instructions-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.dotfiles = self.home / ".dotfiles"
        (self.dotfiles / "agents").mkdir(parents=True)
        (self.dotfiles / "scripts").mkdir()
        (self.dotfiles / "scripts/lib").symlink_to(REPO / "scripts/lib")
        self.source = self.dotfiles / "agents/AGENTS.md"
        self.source.write_text("# Shared\n")
        self.env = dict(os.environ, HOME=str(self.home))
        for key in ("BASH_ENV", "ENV"):
            self.env.pop(key, None)

    def run_sync(self, *args):
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts/sync-agent-instructions.sh"), *args],
            cwd=self.root, env=self.env, input="", text=True,
            capture_output=True, timeout=20,
        )

    def state(self, tool_home, path):
        script = 'source "$HOME/.dotfiles/scripts/lib/agent-instructions.sh"; agent_instruction_state "$1" "$2"'
        result = subprocess.run(
            ["/bin/bash", "-c", script, "state", str(tool_home), str(path)],
            env=self.env, text=True, capture_output=True, timeout=20, check=True,
        )
        return result.stdout.strip()

    def snapshot(self):
        result = {}
        for directory, dirs, files in os.walk(self.home, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink():
                    result[str(path)] = ("link", os.readlink(path))
                elif path.is_file():
                    result[str(path)] = ("file", path.read_bytes())
                else:
                    result[str(path)] = ("dir",)
        return result

    def test_only_installed_tools_are_linked_and_rerun_is_idempotent(self):
        (self.home / ".copilot").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex/AGENTS.md").write_text("")

        first = self.run_sync()
        self.assertEqual(first.returncode, 0, first.stderr)
        for path in (self.home / ".copilot/copilot-instructions.md", self.home / ".codex/AGENTS.md"):
            self.assertEqual(os.readlink(path), str(self.source))
            self.assertEqual(path.read_text(), "# Shared\n")
        self.assertFalse(os.path.lexists(self.home / ".claude"))
        self.assertIn("2 change(s), 0 conflict(s), 0 error(s)", first.stdout)

        before = self.snapshot()
        second = self.run_sync()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("0 change(s)", second.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_stale_managed_and_dangling_links_are_repaired(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".claude/CLAUDE.md").symlink_to(self.dotfiles / "old/CLAUDE.md")
        (self.home / ".codex/AGENTS.md").symlink_to(self.root / "gone.md")

        result = self.run_sync("--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 target(s) linked", result.stdout)
        self.assertEqual(os.readlink(self.home / ".claude/CLAUDE.md"), str(self.source))
        self.assertEqual(os.readlink(self.home / ".codex/AGENTS.md"), str(self.source))

    def test_quiet_rerun_reports_instructions_are_up_to_date(self):
        (self.home / ".copilot").mkdir()
        first = self.run_sync("--quiet")
        self.assertEqual(first.returncode, 0, first.stderr)

        result = self.run_sync("--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agent instructions: up to date", result.stdout)

    def test_existing_content_and_foreign_links_are_preserved_as_conflicts(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".claude/CLAUDE.md").write_text("Local rule\n")
        foreign = self.root / "elsewhere.md"
        foreign.write_text("Foreign\n")
        (self.home / ".codex/AGENTS.md").symlink_to(foreign)
        before = self.snapshot()

        result = self.run_sync("--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(foreign.read_text(), "Foreign\n")
        self.assertIn("conflict: Claude Code", result.stderr)
        self.assertIn("conflict: Codex", result.stderr)
        self.assertIn("0 change(s), 2 conflict(s), 0 error(s)", result.stdout)

    def test_dry_run_reports_without_changes(self):
        (self.home / ".copilot").mkdir()
        before = self.snapshot()

        result = self.run_sync("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would link: Copilot CLI", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_missing_source_or_unknown_option_fails_without_changes(self):
        (self.home / ".copilot").mkdir()
        before = self.snapshot()
        unknown = self.run_sync("--bogus")
        self.assertEqual(self.snapshot(), before)
        self.source.unlink()
        before = self.snapshot()
        missing = self.run_sync()

        self.assertEqual(unknown.returncode, 2)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("source missing", missing.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_state_classifies_each_target_kind(self):
        tool = self.home / ".tool"
        path = tool / "AGENTS.md"
        self.assertEqual(self.state(tool, path), "absent")
        tool.mkdir()
        self.assertEqual(self.state(tool, path), "missing")
        path.write_text("")
        self.assertEqual(self.state(tool, path), "empty")
        path.write_text("rule\n")
        self.assertEqual(self.state(tool, path), "file")
        path.unlink()
        path.symlink_to(self.source)
        self.assertEqual(self.state(tool, path), "linked")
        path.unlink()
        path.symlink_to(self.root)
        self.assertEqual(self.state(tool, path), "foreign")


if __name__ == "__main__":
    unittest.main()
