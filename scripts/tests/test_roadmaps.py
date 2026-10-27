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

    def make_roadmap(self, relative, briefs, index="ROADMAP.md", brief_dir="roadmap"):
        root = self.home / relative
        root.mkdir(parents=True)
        (root / ".git").mkdir()
        (root / index).parent.mkdir(parents=True, exist_ok=True)
        options = ["--root", str(root), "--index", index, "--dir", brief_dir]
        subprocess.run([PYTHON, str(SKILL), "init", *options],
                       capture_output=True, text=True, check=True)
        for slug, (front_matter, body) in briefs.items():
            backlink = os.path.relpath(root / index, root / brief_dir)
            (root / brief_dir / f"{slug}.md").write_text(
                f"---\n{front_matter}\n---\n# Item\n\n[Roadmap]({backlink})\n{body}")
        subprocess.run([PYTHON, str(SKILL), "sync", *options],
                       capture_output=True, text=True, check=True)

    def test_discovers_custom_locations_and_casing(self):
        layouts = [
            ("lower", "roadmap.md", "roadmap"),
            ("plural", "docs/roadmaps.md", "docs/roadmap"),
            ("docs", "docs/roadmap.md", "docs/roadmap"),
            ("upper", "docs/ROADMAP.md", "roadmap"),
            ("tasks", "tasks/roadmap.md", "tasks/roadmap"),
            ("planning", "planning/Roadmap.md", "planning/roadmap"),
            ("mixed", "Docs/roadmap.md", "Docs/Roadmap"),
            ("root-briefs", "Docs/roadmap.md", "Roadmap"),
        ]
        for name, index, brief_dir in layouts:
            self.make_roadmap(f"repos/{name}", {
                name: (f"title: Working on {name}\nstatus: active", ""),
            }, index=index, brief_dir=brief_dir)

        for paths in [(str(self.home / "repos"),),
                      tuple(str(self.home / "repos" / name) for name, _, _ in layouts)]:
            with self.subTest(paths=paths):
                result = self.run_roadmaps(*paths)
                self.assertEqual(result.returncode, 0, result.stdout)
                for name, _, _ in layouts:
                    self.assertIn(f"─ {name}  ~/repos/{name} ", result.stdout)
                    self.assertIn(f"    {name}: Working on {name}", result.stdout)
                self.assertNotIn("error(s)", result.stdout)

    def test_collection_is_not_mistaken_for_repository(self):
        for name in ("docs", "tasks", "planning"):
            self.make_roadmap(f"repos/{name}", {
                name: (f"title: Working on {name}\nstatus: active", ""),
            })
        self.make_roadmap("repos/app", {"broken": ("title: Broken\nstatus: bogus", "")})
        result = self.run_roadmaps(str(self.home / "repos"))
        self.assertEqual(result.returncode, 1, result.stdout)
        for name in ("docs", "tasks", "planning", "app"):
            self.assertIn(f"─ {name}  ~/repos/{name} ", result.stdout)
        self.assertIn("error(s); run the roadmap skill's `check`", result.stdout)
        self.assertNotIn("─ repos ", result.stdout)

    def test_symlinked_index_alias_is_checked_only_once(self):
        self.make_roadmap("repos/project", {"ship": ("title: Ship\nstatus: active", "")})
        docs = self.home / "repos/project/docs"
        docs.mkdir()
        (docs / "roadmap.md").symlink_to("../ROADMAP.md")
        result = self.run_roadmaps(str(self.home / "repos"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.count("    ship: Ship"), 1, result.stdout)

    def test_nested_roadmap_is_deduplicated_and_still_requires_marker(self):
        self.make_roadmap("repos/project", {
            "review": ("title: Review\nstatus: verifying", "\n## Acceptance\n"),
        }, index="docs/roadmap.md", brief_dir="docs/roadmap")
        (self.home / "repos/project/ROADMAP.md").write_text("# See docs/roadmap.md\n")
        (self.home / "repos/foreign/docs").mkdir(parents=True)
        (self.home / "repos/foreign/docs/roadmaps.md").write_text("# Other layout\n")

        result = self.run_roadmaps(config="~/repos\n~/repos/project\n~/repos/project/docs\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.count("    review: Review"), 1, result.stdout)
        self.assertIn("─ project  ~/repos/project ", result.stdout)
        self.assertNotIn("foreign", result.stdout)

    def test_missing_nested_briefs_are_reported(self):
        self.make_roadmap("repos/project", {}, index="docs/roadmap.md", brief_dir="docs/roadmap")
        (self.home / "repos/project/docs/roadmap").rename(
            self.home / "repos/project/docs/moved")
        result = self.run_roadmaps(str(self.home / "repos"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("─ project  ~/repos/project ", result.stdout)
        self.assertIn("error(s); run the roadmap skill's `check`", result.stdout)

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
        self.assertTrue(out.startswith("2026-10-21\n"), out)
        busy = out.split("─ busy  ~/repos/busy ")[1].split("\n─ ")[0]
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
        self.assertIn("─ broken  ~/code/broken ", result.stdout)
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
        self.assertIn("─ a-bad-brief  ~/repos/a-bad-brief ─────────────────────────\n  ✗ cannot read the roadmap:", result.stdout)
        self.assertIn("  ✗ error: ~/repos/b-bad-index/ROADMAP.md: cannot read (UnicodeDecodeError)",
                      result.stdout)
        self.assertIn("  ▸ In progress\n    y: Y", result.stdout, "later repositories are still shown")


if __name__ == "__main__":
    unittest.main()
