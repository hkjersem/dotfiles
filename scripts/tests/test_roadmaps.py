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

    def run_roadmaps(self, *args, config=None, cwd=None):
        if config is not None:
            path = self.home / ".config/dotfiles/roadmap-paths"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config)
        return subprocess.run([PYTHON, str(SCRIPT), *args, "--today", "2026-10-21"],
                              capture_output=True, text=True, check=False, cwd=cwd,
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
        self.assertIn("error(s); run roadmaps --check ", result.stdout)
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
        self.assertIn("error(s); run roadmaps --check ", result.stdout)

    def test_check_accepts_either_argument_order_and_preserves_files(self):
        self.make_roadmap("repo with spaces", {
            "broken": ("title: Broken\nstatus: active", "[Missing](../missing.md)\n"),
        }, index="docs/roadmaps.md", brief_dir="docs/roadmap")
        root = self.home / "repo with spaces"
        before = {path.relative_to(root): path.read_bytes()
                  for path in root.rglob("*") if path.is_file()}
        for args in [("--check", "."), (".", "--check")]:
            with self.subTest(args=args):
                result = self.run_roadmaps(*args, cwd=root)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("docs/roadmap/broken.md: broken link to ../missing.md", result.stdout)
                self.assertNotIn("In progress", result.stdout)
                self.assertNotIn("Valid roadmap:", result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertEqual({path.relative_to(root): path.read_bytes()
                                  for path in root.rglob("*") if path.is_file()}, before)

    def test_check_without_paths_validates_configured_roadmaps(self):
        self.make_roadmap("repos/clean", {"working": ("title: Working\nstatus: active", "")})
        self.make_roadmap("repos/broken", {"bad": ("title: Bad\nstatus: shipped", "")})
        result = self.run_roadmaps("--check", config="~/repos\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Valid roadmap: ROADMAP.md (1 open, 0 archived)", result.stdout)
        self.assertIn("roadmap/bad.md: status `shipped` is not one of", result.stdout)
        self.assertNotIn("In progress", result.stdout)
        self.assertNotIn("Nothing needs attention", result.stdout)

        result = self.run_roadmaps("--check", str(self.home / "repos/clean"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Valid roadmap:", result.stdout)

    def test_strict_check_reports_quality_warnings_without_writes_or_failure(self):
        self.make_roadmap("repo", {
            "working": ("title: Working\nstatus: active", "Touches `src/missing.py`.\n"),
        }, index="docs/roadmap.md", brief_dir="docs/roadmap")
        root = self.home / "repo"
        before = {p.relative_to(root): p.read_bytes()
                  for p in root.rglob("*") if p.is_file()}
        normal = self.run_roadmaps("--check", ".", cwd=root)
        self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
        self.assertNotIn("missing `summary`", normal.stdout)
        for args, config in [
                (("--check", "--strict", "."), None),
                ((".", "--check", "--strict"), None),
                (("--check", "--strict"), "~/repo\n")]:
            with self.subTest(args=args):
                result = self.run_roadmaps(*args, config=config, cwd=root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for warning in ("missing `summary`", "no `## Acceptance` section",
                                "no `## Progress` section",
                                "code pointer `src/missing.py` does not exist"):
                    self.assertIn(warning, result.stdout)
                self.assertIn("Valid roadmap:", result.stdout)
                self.assertEqual({p.relative_to(root): p.read_bytes()
                                  for p in root.rglob("*") if p.is_file()}, before)

    def test_strict_requires_check_and_still_fails_on_concrete_errors(self):
        self.make_roadmap("repo", {"bad": ("title: Bad\nstatus: shipped", "")})
        root = self.home / "repo"
        for args in [("--strict", "."), ("--sync", "--strict", ".")]:
            result = self.run_roadmaps(*args, cwd=root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("--strict requires --check", result.stderr)
        result = self.run_roadmaps("--check", "--strict", ".", cwd=root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("status `shipped` is not one of", result.stdout)
        self.assertNotIn("Valid roadmap:", result.stdout)

    def test_check_does_not_treat_missing_roadmaps_as_valid(self):
        self.make_roadmap("valid", {})
        empty = self.home / "empty"
        empty.mkdir()
        (empty / "ROADMAP.md").write_text("# Hand-written plans\n")
        for args, config in [
                (("--check", str(empty)), None),
                (("--check",), "~/valid\n~/empty\n~/missing\n")]:
            with self.subTest(args=args):
                result = self.run_roadmaps(*args, config=config)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("~/empty: no managed roadmap found", result.stdout)
                if config:
                    self.assertIn("~/missing: not a directory", result.stdout)
                    self.assertIn("Valid roadmap:", result.stdout)
                else:
                    self.assertNotIn("Valid roadmap:", result.stdout)

    def test_overview_error_hint_quotes_paths_with_spaces(self):
        self.make_roadmap("repo with spaces", {"bad": ("title: Bad\nstatus: shipped", "")})
        result = self.run_roadmaps(str(self.home / "repo with spaces"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            f"run roadmaps --check '{self.home / 'repo with spaces'}'", result.stdout)

    def test_check_without_configured_paths_is_not_success(self):
        result = self.run_roadmaps("--check", config="# no locations yet\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No roadmaps found.", result.stdout)
        self.assertNotIn("Valid roadmap:", result.stdout)

    def test_sync_discovers_layout_and_only_updates_generated_index(self):
        self.make_roadmap("repo", {"ship": ("title: Ship\nstatus: planned", "")},
                          index="docs/roadmap.md", brief_dir="docs/roadmap")
        root = self.home / "repo"
        index = root / "docs/roadmap.md"
        item = root / "docs/roadmap/ship.md"
        for args in [("--sync", "."), (".", "--sync")]:
            with self.subTest(args=args):
                item.write_text(item.read_text().replace("status: planned", "status: active"))
                before = {p.relative_to(root): p.read_bytes()
                          for p in root.rglob("*") if p.is_file()}
                result = self.run_roadmaps(*args, cwd=root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Updated docs/roadmap.md.", result.stdout)
                self.assertIn("[Ship](roadmap/ship.md) · active", index.read_text())
                for path, content in before.items():
                    if path != Path("docs/roadmap.md"):
                        self.assertEqual((root / path).read_bytes(), content)
                original_index = before[Path("docs/roadmap.md")].decode()
                self.assertEqual(index.read_text().split("<!-- roadmap:start -->")[0],
                                 original_index.split("<!-- roadmap:start -->")[0])
                self.assertEqual(index.read_text().split("<!-- roadmap:end -->")[1],
                                 original_index.split("<!-- roadmap:end -->")[1])
                check = self.run_roadmaps("--check", ".", cwd=root)
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
                again = self.run_roadmaps(*args, cwd=root)
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                self.assertIn("is up to date", again.stdout)
                item.write_text(item.read_text().replace("status: active", "status: planned"))
                subprocess.run([PYTHON, str(SKILL), "sync", "--root", str(root),
                                "--index", "docs/roadmap.md", "--dir", "docs/roadmap"],
                               capture_output=True, check=True)

    def test_sync_requires_explicit_paths_and_rejects_check_combination(self):
        self.make_roadmap("repo", {})
        for args in [("--sync",), ("--sync", "--check", str(self.home / "repo"))]:
            result = self.run_roadmaps(*args, config="~/repo\n")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        result = self.run_roadmaps("--sync", str(self.home / "missing"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_sync_does_not_overwrite_index_with_invalid_or_missing_briefs(self):
        for failure in ("invalid", "missing", "markers"):
            with self.subTest(failure=failure):
                self.make_roadmap(failure, {"ship": ("title: Ship\nstatus: planned", "")})
                root = self.home / failure
                index = root / "ROADMAP.md"
                if failure == "invalid":
                    (root / "roadmap/ship.md").write_text("# No front matter\n")
                elif failure == "missing":
                    (root / "roadmap").rename(root / "moved")
                else:
                    index.write_text(index.read_text().replace("<!-- roadmap:end -->", ""))
                before = index.read_bytes()
                result = self.run_roadmaps("--sync", str(root))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(index.read_bytes(), before)
                self.assertNotIn("Valid roadmap:", result.stdout)

    def test_check_stale_index_suggests_simple_sync_command(self):
        self.make_roadmap("repo with spaces", {})
        root = self.home / "repo with spaces"
        (root / "roadmap/new.md").write_text("---\ntitle: New\nstatus: planned\n---\n")
        result = self.run_roadmaps("--check", str(root))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"roadmaps --sync '{root}'", result.stdout)
        self.assertNotIn("run `sync`", result.stdout)

    def test_sync_reports_remaining_errors_and_continues_to_other_repositories(self):
        for name in ("broken", "fine"):
            self.make_roadmap(f"repos/{name}", {})
            root = self.home / "repos" / name
            body = "[Missing](../missing.md)\n" if name == "broken" else ""
            (root / "roadmap/new.md").write_text(
                "---\ntitle: New\nstatus: active\n---\n" + body)
        result = self.run_roadmaps("--sync", str(self.home / "repos"),
                                   str(self.home / "repos/fine"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("broken link to ../missing.md", result.stdout)
        self.assertEqual(result.stdout.count("Updated ROADMAP.md."), 2)
        self.assertEqual(result.stdout.count("Valid roadmap:"), 1)
        for name in ("broken", "fine"):
            self.assertIn("[New](roadmap/new.md) · active",
                          (self.home / "repos" / name / "ROADMAP.md").read_text())

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
        self.assertIn("  ✗ 1 error(s); run roadmaps --check ", result.stdout)
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
