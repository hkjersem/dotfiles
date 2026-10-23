"""Tests for scripts/roadmaps.py using disposable repositories and HOME."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/roadmaps.py"
SKILL = REPO / "agents/skills/roadmap/scripts/roadmap.py"
PYTHON = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


class RoadmapsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="roadmaps-test-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def run_roadmaps(self, *args, config=None):
        if config is not None:
            path = self.home / ".config/dotfiles/roadmap-paths"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config)
        return subprocess.run([PYTHON, str(SCRIPT), *args, "--today", "2026-10-21"],
                              capture_output=True, text=True, check=False,
                              env={**os.environ, "HOME": str(self.home)})

    def make_roadmap(self, relative, briefs):
        root = self.home / relative
        root.mkdir(parents=True)
        subprocess.run([PYTHON, str(SKILL), "init", "--root", str(root)],
                       capture_output=True, text=True, check=True)
        for slug, (front_matter, body) in briefs.items():
            (root / "roadmap" / f"{slug}.md").write_text(
                f"---\n{front_matter}\n---\n# Item\n\n[Roadmap](../ROADMAP.md)\n{body}")
        subprocess.run([PYTHON, str(SKILL), "sync", "--root", str(root)],
                       capture_output=True, text=True, check=True)

    def test_summarises_roadmaps_in_a_folder(self):
        self.make_roadmap("repos/busy", {
            "weekly": ("title: Weekly\nstatus: ready\nkind: routine\nevery: 1w\nlast_done: 2026-10-01", ""),
            "launch": ("title: Launch\nstatus: planned\ndue: 2026-10-23", ""),
            "later": ("title: Later\nstatus: planned\ndue: 2026-12-01", ""),
            "built": ("title: Built\nstatus: verifying", "\n## Acceptance\n"),
            "doing": ("title: Doing\nstatus: active\nblocked: needs key", ""),
        })
        self.make_roadmap("repos/calm", {"spec": ("title: Spec\nstatus: ready", "")})
        self.make_roadmap("repos/.hidden", {"x": ("title: X\nstatus: active", "")})
        (self.home / "repos/other").mkdir()
        (self.home / "repos/other/ROADMAP.md").write_text("# Hand-written roadmap\n")

        result = self.run_roadmaps(str(self.home / "repos"))
        self.assertEqual(result.returncode, 0, result.stdout)
        out = result.stdout
        self.assertIn("Roadmaps, 2026-10-21", out)
        busy = out.split("── busy  ~/repos/busy ")[1].split("\n── ")[0]
        self.assertIn("    weekly: overdue since 2026-10-08 (13 day(s))", busy)
        self.assertIn("    launch: due 2026-10-23 (in 2 day(s))", busy)
        self.assertNotIn("later", busy, "dates beyond a week are not due yet")
        self.assertIn("  ▸ Awaiting your acceptance\n    built: Built", busy)
        self.assertIn("  ▸ In progress\n    doing: Doing, blocked: needs key", busy)
        self.assertIn("  ✓ Nothing needs attention: calm (1 ready to start)", out)
        self.assertNotIn("hidden", out)
        self.assertNotIn("other", out)

    def test_reads_config_and_reports_problems(self):
        missing = self.run_roadmaps()
        self.assertEqual(missing.returncode, 1)
        self.assertIn("~/.config/dotfiles/roadmap-paths does not exist", missing.stdout)

        self.make_roadmap("code/broken", {"bad": ("title: Bad\nstatus: shipped", "")})
        self.make_roadmap("solo", {})
        (self.home / "empty").mkdir()
        result = self.run_roadmaps(config="# mine\n~/code\n\n~/solo\n~/solo/\n~/empty\n~/gone\n")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("── broken  ~/code/broken ", result.stdout)
        self.assertIn("  ✗ 1 error(s); run the roadmap skill's `check`", result.stdout)
        self.assertIn("Nothing needs attention: solo\n", result.stdout, "listed twice, shown once")
        self.assertIn("  • note: ~/gone: not a directory", result.stdout)
        self.assertNotIn("empty", result.stdout, "folders without roadmaps are skipped quietly")
        self.assertNotIn("mine", result.stdout)

    def test_unreadable_roadmaps_are_reported_without_stopping(self):
        self.make_roadmap("repos/a-bad-brief", {"x": ("title: X\nstatus: active", "")})
        (self.home / "repos/a-bad-brief/roadmap/x.md").write_bytes(b"---\ntitle: \xff\n---\n")
        self.make_roadmap("repos/b-bad-index", {})
        (self.home / "repos/b-bad-index/ROADMAP.md").write_bytes(b"\xff<!-- roadmap:start -->")
        self.make_roadmap("repos/c-fine", {"y": ("title: Y\nstatus: active", "")})

        result = self.run_roadmaps(str(self.home / "repos"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("── a-bad-brief  ~/repos/a-bad-brief ──────────────────────────────────\n  ✗ cannot read the roadmap:", result.stdout)
        self.assertIn("  ✗ error: ~/repos/b-bad-index/ROADMAP.md: cannot read (UnicodeDecodeError)",
                      result.stdout)
        self.assertIn("  ▸ In progress\n    y: Y", result.stdout, "later repositories are still shown")


if __name__ == "__main__":
    unittest.main()
