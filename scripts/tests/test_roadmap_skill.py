"""Roadmap skill script tests using disposable repositories."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "agents/skills/roadmap/scripts/roadmap.py"
# The skill promises compatibility with macOS's bundled Python 3.9.
PYTHON = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def brief(front_matter, body="[Roadmap](../ROADMAP.md)\n"):
    return "---\n" + textwrap.dedent(front_matter).strip() + "\n---\n# Item\n\n" + body


class RoadmapScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="roadmap-skill-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_script(self, *args):
        return subprocess.run(
            [PYTHON, str(SCRIPT), *args, "--root", str(self.root)],
            capture_output=True, text=True, check=False,
        )

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def init(self):
        result = self.run_script("init")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def block(self, index="ROADMAP.md"):
        text = (self.root / index).read_text()
        return text[text.index("<!-- roadmap:start -->"):text.index("<!-- roadmap:end -->")]

    def test_init_creates_layout_and_refuses_to_overwrite(self):
        self.init()
        self.assertTrue((self.root / "roadmap/archive").is_dir())
        self.assertIn("_Nothing committed._", self.block())
        self.assertEqual(self.run_script("check").returncode, 0)

        (self.root / "ROADMAP.md").write_text("# Mine\n")
        result = self.run_script("init")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not overwriting", result.stdout)
        self.assertEqual((self.root / "ROADMAP.md").read_text(), "# Mine\n")

    def test_sync_groups_by_status_and_is_idempotent(self):
        self.init()
        self.write("roadmap/live.md", brief("title: Live\nstatus: active\nblocked: waiting on key"))
        self.write("roadmap/spec.md", brief("title: Spec\nstatus: ready\nsummary: Fully specified."))
        self.write("roadmap/open.md", brief("title: Open\nstatus: planned\nkind: debt"))
        self.write("roadmap/parked.md", brief("title: Parked\nstatus: ready\nwhen: after release"))
        self.write("roadmap/idea.md", brief("title: Idea\nstatus: idea"))
        self.write("roadmap/archive/base.md", brief("title: Base\nstatus: done", "[Roadmap](../../ROADMAP.md)\n"))
        self.write("roadmap/after.md", brief("title: After\nstatus: planned\ndepends_on: [base, open]"))
        self.assertEqual(self.run_script("sync").returncode, 0)

        block = self.block()
        self.assertIn("[After](roadmap/after.md) · planned · after: open\n", block,
                      "only unfinished dependencies are shown")
        now, rest = block.split("## Next")
        nxt, later = rest.split("## Later")
        self.assertIn("[Live](roadmap/live.md) · active · **blocked:** waiting on key", now)
        self.assertLess(nxt.index("[Spec]"), nxt.index("[Open]"), "ready items come first")
        self.assertIn("planned · debt", nxt)
        self.assertIn("[Parked](roadmap/parked.md) · ready · when: after release", later)
        self.assertIn("[Idea]", later)

        before = (self.root / "ROADMAP.md").read_text()
        self.assertIn("up to date", self.run_script("sync").stdout)
        self.assertEqual((self.root / "ROADMAP.md").read_text(), before)
        self.assertEqual(self.run_script("check").returncode, 0)

    def test_sync_preserves_hand_written_sections(self):
        self.init()
        index = self.root / "ROADMAP.md"
        index.write_text(index.read_text().replace(
            "## Not doing\n", "## Not doing\n\n- **Plugins**: out of scope.\n"))
        self.write("roadmap/a.md", brief("title: A\nstatus: planned"))
        self.run_script("sync")
        self.assertIn("- **Plugins**: out of scope.", index.read_text())

    def test_check_reports_stale_index_and_misplaced_statuses(self):
        self.init()
        self.write("roadmap/unsynced.md", brief("title: Unsynced\nstatus: planned"))
        self.write("roadmap/finished.md", brief("title: Finished\nstatus: done"))
        self.write("roadmap/archive/reopened.md",
                   brief("title: Reopened\nstatus: active", "[Roadmap](../../ROADMAP.md)\n"))
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("generated lists are out of date", result.stdout)
        self.assertIn("status `done` but not archived", result.stdout)
        self.assertIn("run `archive finished`", result.stdout)
        self.assertIn("archived brief has open status `active`", result.stdout)

    def test_check_validates_dependencies(self):
        self.init()
        self.write("roadmap/a.md", brief("title: A\nstatus: planned\ndepends_on: [b]"))
        self.write("roadmap/b.md", brief("title: B\nstatus: planned\ndepends_on: [a]"))
        self.write("roadmap/c.md", brief("title: C\nstatus: planned\ndepends_on: [missing]"))
        self.write("roadmap/archive/gone.md",
                   brief("title: Gone\nstatus: dropped", "[Roadmap](../../ROADMAP.md)\n"))
        self.write("roadmap/d.md", brief("title: D\nstatus: planned\ndepends_on: [gone]"))
        self.run_script("sync")
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("dependency cycle"), 2)
        self.assertIn("unknown item `missing`", result.stdout)
        self.assertIn("dropped item `gone`", result.stdout)

    def test_front_matter_rejects_unsupported_syntax(self):
        self.init()
        cases = {
            "list-status": ("status: [ready]", "single value, not a list"),
            "scalar-deps": ("status: idea\ndepends_on: a", "one-line list"),
            "colon": ("status: idea\nsummary: a: b", "contains ': '"),
            "escape": ('status: idea\nsummary: "a \\"b\\""', "escapes are not supported"),
            "block": ("status: idea\nsummary: |", "unsupported value syntax"),
            "unknown-key": ("status: idea\nowner: me", "unknown front matter key"),
            "bad-status": ("status: wip", "not one of"),
        }
        for slug, (lines, _) in cases.items():
            self.write(f"roadmap/{slug}.md", brief(f"title: T\n{lines}"))
        self.write("roadmap/none.md", "# No front matter\n")
        output = self.run_script("check").stdout
        for slug, (_, message) in cases.items():
            with self.subTest(slug):
                self.assertRegex(output, rf"roadmap/{slug}\.md: [^\n]*{message}")
        self.assertIn("roadmap/none.md: no front matter", output)

    def test_front_matter_accepts_supported_forms(self):
        self.init()
        self.write("roadmap/quoted.md", brief(
            "title: 'It''s here: now'\nstatus: idea\nsummary: see https://example.com/x\n"
            "# a comment\ndepends_on:"))
        self.write("roadmap/crlf.md", "\ufeff" + brief("title: CRLF\nstatus: idea").replace("\n", "\r\n"))
        self.run_script("sync")
        result = self.run_script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[It's here: now](roadmap/quoted.md)", self.block())
        self.assertIn("[CRLF](roadmap/crlf.md)", self.block())

    def test_ready_separates_startable_open_and_waiting(self):
        self.init()
        self.write("roadmap/base.md", brief("title: Base\nstatus: ready"))
        self.write("roadmap/next.md", brief("title: Next\nstatus: ready\ndepends_on: [base]"))
        self.write("roadmap/open.md", brief("title: Open\nstatus: planned"))
        self.write("roadmap/stuck.md", brief("title: Stuck\nstatus: ready\nblocked: needs access"))
        self.write("roadmap/parked.md", brief("title: Parked\nstatus: ready\nwhen: later"))
        self.write("roadmap/work.md", brief("title: Work\nstatus: active"))
        result = self.run_script("ready")
        self.assertEqual(result.returncode, 0, result.stdout)
        out = result.stdout
        self.assertIn("In progress (reported, not locked):\n  work: active", out)
        self.assertIn("Ready to start:\n  base: Base\n", out)
        self.assertIn("Committed, decisions still open:\n  open: Open", out)
        self.assertIn("next: waiting for base", out)
        self.assertIn("stuck: blocked: needs access", out)
        self.assertNotIn("parked", out)

    def test_ready_reports_invalid_records(self):
        self.init()
        self.write("roadmap/a.md", brief("title: A\nstatus: ready\ndepends_on: [nope]"))
        result = self.run_script("ready")
        self.assertEqual(result.returncode, 1)
        self.assertIn("a: invalid dependency nope", result.stdout)
        self.assertIn("unknown item `nope`", result.stdout)

    def test_foreign_layout_is_left_alone(self):
        index = self.write("ROADMAP.md", "| ID | Status |\n| --- | --- |\n| M01 | Done |\n")
        item = self.write("roadmap/01-thing.md", "# Thing\n\nStatus: Implemented\n")
        for command in ("sync", "check", "ready"):
            with self.subTest(command):
                result = self.run_script(command)
                self.assertEqual(result.returncode, 2)
                self.assertIn("different roadmap format", result.stdout)
                self.assertNotIn("error:", result.stdout)
        self.assertIn("| M01 | Done |", index.read_text())
        self.assertEqual(item.read_text(), "# Thing\n\nStatus: Implemented\n")

        # One brief with front matter means a drifting roadmap, which gets full errors.
        self.write("roadmap/02-new.md", brief("title: New\nstatus: idea"))
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("roadmap markers", result.stdout)
        self.assertIn("roadmap/01-thing.md: no front matter", result.stdout)

    def test_foreign_layout_detection_edges(self):
        # Front matter without a status, or only in a README, is not this layout.
        self.write("ROADMAP.md", "# Plans\n")
        self.write("roadmap/page.md", "---\nlayout: page\n---\n# Page\n")
        self.write("roadmap/README.md", "---\nstatus: draft\n---\n")
        self.assertEqual(self.run_script("check").returncode, 2)

        # A lone marker is a damaged roadmap of this layout, so explain it.
        (self.root / "roadmap/page.md").unlink()
        (self.root / "roadmap/README.md").unlink()
        self.write("ROADMAP.md", "# Plans\n\n<!-- roadmap:start -->\n")
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("roadmap markers", result.stdout)

    def test_case_mismatches_are_reported_on_case_insensitive_disks(self):
        (self.root / "probe").write_text("")
        if not (self.root / "PROBE").exists():
            self.skipTest("file system is case-sensitive; mismatched names are simply missing")
        (self.root / "probe").unlink()

        self.write("roadmap.md", "# Plan\n\n- [ ] Something\n")
        result = self.run_script("check")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stdout.startswith("roadmap.md uses a different roadmap format"), result.stdout)

        (self.root / "roadmap.md").unlink()
        self.init()
        (self.root / "ROADMAP.md").rename(self.root / "Roadmap.md")
        self.write("roadmap/item.md", brief("""
            title: Item
            status: planned
            summary: Link with the wrong case.
        """, "[Roadmap](../ROADMAP.md)\n"))
        self.assertIn("Updated Roadmap.md.", self.run_script("sync").stdout)
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("index is spelled Roadmap.md, not ROADMAP.md", result.stdout)
        self.assertIn("link to ../ROADMAP.md differs in case from Roadmap.md", result.stdout)

        result = self.run_script("check", "--index", "Roadmap.md")
        self.assertNotIn("index is spelled", result.stdout)
        self.assertIn("differs in case", result.stdout)

    def test_routines_and_due_dates(self):
        self.init()
        self.write("roadmap/review.md", brief(
            "title: Review\nstatus: ready\nkind: routine\nevery: 2w\nlast_done: 2026-09-28",
            "[Roadmap](../ROADMAP.md)\n\n## Log\n\n- 2026-09-28: Done.\n"))
        self.write("roadmap/monthly.md", brief(
            "title: Monthly\nstatus: ready\nkind: routine\nevery: 1m\nlast_done: 2026-01-31"))
        self.write("roadmap/fresh.md", brief("title: Fresh\nstatus: planned\nkind: routine\nevery: 10d"))
        self.write("roadmap/first.md", brief(
            "title: First\nstatus: ready\nkind: routine\nevery: 1w\ndue: 2026-10-20"))
        self.write("roadmap/paused.md", brief(
            "title: Paused\nstatus: ready\nkind: routine\nevery: 1w\nlast_done: 2026-01-01\nwhen: after move"))
        self.write("roadmap/launch.md", brief("title: Launch\nstatus: planned\ndue: 2026-10-09"))
        self.write("roadmap/far.md", brief("title: Far\nstatus: planned\ndue: 2027-01-01"))
        self.assertEqual(self.run_script("sync").returncode, 0)

        block = self.block()
        routines = block.split("## Routines")[1].split("## Later")[0]
        self.assertIn("[Review](roadmap/review.md) · ready · routine · every 2w · due: 2026-10-12", routines)
        self.assertIn("every 1m · due: 2026-02-28", routines, "month end is clamped")
        self.assertIn("[Fresh](roadmap/fresh.md) · planned · routine · every 10d · due: not yet done", routines)
        self.assertIn("every 1w · due: 2026-10-20", routines)
        self.assertNotIn("Paused", routines)
        self.assertIn("[Paused]", block.split("## Later")[1])
        self.assertIn("[Launch](roadmap/launch.md) · planned · due: 2026-10-09", block.split("## Next")[1])
        self.assertEqual(self.run_script("check", "--strict").returncode, 0)

        result = self.run_script("ready", "--today", "2026-10-08")
        self.assertEqual(result.returncode, 0, result.stdout)
        after = result.stdout.split("Due (overdue or within 7 days):\n")[1].splitlines()
        due = "\n".join(line for line in after[:next(i for i, l in enumerate(after)
                                                      if not l.startswith("  "))])
        self.assertEqual(due.splitlines(), [
            "  fresh: routine not done yet",
            "  monthly: overdue since 2026-02-28 (222 day(s))",
            "  launch: due 2026-10-09 (in 1 day(s))",
            "  review: due 2026-10-12 (in 4 day(s))",
        ])
        self.assertNotIn("paused", result.stdout)
        self.assertNotIn("first:", due)
        self.assertNotIn("far:", due)
        self.assertNotIn("fresh: Fresh", result.stdout, "routines are not startable items")

    def test_routine_validation(self):
        self.init()
        cases = {
            "no-every": ("kind: routine\nstatus: ready", "routine needs `every`"),
            "bad-every": ("kind: routine\nstatus: ready\nevery: 2 weeks", "routine needs `every`"),
            "finished": ("kind: routine\nstatus: verifying\nevery: 1w\n", "routines never finish"),
            "stale-due": ("kind: routine\nstatus: ready\nevery: 1w\nlast_done: 2026-10-01\ndue: 2026-09-30",
                          "`due` is not after `last_done`"),
            "bad-date": ("status: planned\ndue: 2026-02-30", "not a valid YYYY-MM-DD date"),
            "one-off-every": ("status: planned\nevery: 1w", "`every` is only for routines"),
        }
        for slug, (lines, _) in cases.items():
            self.write(f"roadmap/{slug}.md", brief(f"title: T\n{lines}"))
        self.run_script("sync")
        output = self.run_script("check").stdout
        for slug, (_, message) in cases.items():
            with self.subTest(slug):
                self.assertRegex(output, rf"roadmap/{slug}\.md: [^\n]*{message}")

        self.write("roadmap/unlogged.md", brief(
            "title: U\nstatus: ready\nkind: routine\nevery: 1w\nlast_done: 2026-10-01"))
        self.assertIn("no `## Log` section", self.run_script("check", "--strict").stdout)

    def test_routine_edge_cases(self):
        self.init()
        self.write("roadmap/clamped.md", brief(
            "title: Clamped\nstatus: ready\nkind: routine\nevery: 1m\nlast_done: 2026-02-28"))
        self.write("roadmap/huge.md", brief(
            "title: Huge\nstatus: ready\nkind: routine\nevery: 1000000m\nlast_done: 2026-01-01"))
        self.write("roadmap/edge.md", brief(
            "title: Edge\nstatus: ready\nkind: routine\nevery: 1d\nlast_done: 9999-12-31"))
        self.write("roadmap/running.md", brief(
            "title: Running\nstatus: active\nkind: routine\nevery: 1m\nlast_done: 2026-10-01"))
        self.write("roadmap/stuck.md", brief(
            "title: Stuck\nstatus: ready\nkind: routine\nevery: 1w\nlast_done: 2026-09-01\n"
            "blocked: no credentials"))
        self.write("roadmap/later.md", brief(
            "title: Later\nstatus: ready\nkind: routine\nevery: 1m\nlast_done: 2026-10-01\n"
            "blocked: no access"))
        self.write("roadmap/needs.md", brief("title: Needs\nstatus: ready\ndepends_on: [running]"))
        sync = self.run_script("sync")
        self.assertNotIn("Traceback", sync.stdout + sync.stderr)
        self.assertIn("[Clamped](roadmap/clamped.md) · ready · routine · every 1m · due: 2026-03-28",
                      self.block(), "the clamped day carries forward")
        self.assertIn("[Huge](roadmap/huge.md) · ready · routine · every 1000000m · due: invalid",
                      self.block())

        check = self.run_script("check")
        self.assertNotIn("Traceback", check.stderr)
        self.assertEqual(check.stdout.count("not a representable date"), 2)
        self.assertIn("depends on routine `running`, which never finishes", check.stdout)

        out = self.run_script("ready", "--today", "2026-10-08").stdout
        self.assertIn("In progress (reported, not locked):\n  running: active", out)
        self.assertIn("  stuck: overdue since 2026-09-08 (30 day(s)) (blocked: no credentials)", out)
        waiting = out.split("Waiting:\n")[1]
        self.assertIn("  stuck: blocked: no credentials", waiting)
        self.assertIn("  later: blocked: no access", waiting, "blocked routines show even when not due")
        self.assertNotIn("Ready to start", out)

    def test_ready_annotates_parked_and_uncommitted_due_items(self):
        self.init()
        self.write("roadmap/parked.md", brief(
            "title: Parked\nstatus: ready\nwhen: after release\ndue: 2026-10-12"))
        self.write("roadmap/maybe.md", brief("title: Maybe\nstatus: idea\ndue: 2026-10-13"))
        self.write("roadmap/go.md", brief("title: Go\nstatus: ready\ndue: 2026-10-14"))
        self.run_script("sync")
        result = self.run_script("ready", "--today", "2026-10-12")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("parked: due today (parked until: after release)", result.stdout)
        self.assertIn("maybe: due 2026-10-13 (in 1 day(s)) (idea, not committed)", result.stdout)
        self.assertIn("go: due 2026-10-14 (in 2 day(s))\n", result.stdout)

    def test_archive_requires_closed_status(self):
        self.init()
        source = self.write("roadmap/a.md", brief("title: A\nstatus: verifying"))
        result = self.run_script("archive", "a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has status `verifying`", result.stdout)
        self.assertTrue(source.exists())

    def test_archive_checks_index_before_moving(self):
        self.init()
        source = self.write("roadmap/a.md", brief("title: A\nstatus: done"))
        (self.root / "ROADMAP.md").write_text("# No markers\n")
        result = self.run_script("archive", "a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("lacks one pair of roadmap markers", result.stdout)
        self.assertTrue(source.exists())
        self.assertFalse((self.root / "roadmap/archive/a.md").exists())

    def test_archive_rewrites_links_outside_code(self):
        self.init()
        self.write("src/app.py", "")
        self.write("roadmap/dep.md", brief("title: Dep\nstatus: done", textwrap.dedent("""\
            [Roadmap](../ROADMAP.md) [self](dep.md#outcome) [sib](other.md 'title')
            ![image](../src/app.py) [angle](<other.md>)
            [ref]: other.md

            ```
            [sample](other.md)
            ```
            Inline `[x](other.md)` stays.
            ## Outcome
            """)))
        self.write("roadmap/other.md", brief(
            "title: Other\nstatus: planned\ndepends_on: [dep]",
            "[Roadmap](../ROADMAP.md) [dep](dep.md#outcome) [again](./dep.md)\n"))
        self.write("AGENTS.md", "See [dep](roadmap/dep.md) and `roadmap/dep.md`.\n")
        self.write("node_modules/pkg/README.md", "[dep](../../roadmap/dep.md)\n")
        self.write(".github/agents/planner.agent.md", "[dep](../../roadmap/dep.md)\n")
        self.write(".venv/notes.md", "[dep](../roadmap/dep.md)\n")
        self.run_script("sync")

        result = self.run_script("archive", "dep")
        self.assertEqual(result.returncode, 0, result.stdout)
        for path in ("AGENTS.md", ".github/agents/planner.agent.md", "roadmap/other.md"):
            self.assertIn(path, result.stdout)
        self.assertNotIn(".venv", result.stdout)
        self.assertFalse((self.root / "roadmap/dep.md").exists())

        moved = (self.root / "roadmap/archive/dep.md").read_text()
        for expected in ("[Roadmap](../../ROADMAP.md)", "[self](dep.md#outcome)",
                         "[sib](../other.md 'title')", "![image](../../src/app.py)",
                         "[angle](<../other.md>)", "[ref]: ../other.md",
                         "[sample](other.md)", "`[x](other.md)`"):
            self.assertIn(expected, moved)
        other = (self.root / "roadmap/other.md").read_text()
        self.assertIn("[dep](archive/dep.md#outcome) [again](archive/dep.md)", other)
        self.assertEqual((self.root / "AGENTS.md").read_text(),
                         "See [dep](roadmap/archive/dep.md) and `roadmap/dep.md`.\n")
        self.assertEqual((self.root / "node_modules/pkg/README.md").read_text(),
                         "[dep](../../roadmap/dep.md)\n")
        self.assertEqual((self.root / ".github/agents/planner.agent.md").read_text(),
                         "[dep](../../roadmap/archive/dep.md)\n")
        self.assertEqual((self.root / ".venv/notes.md").read_text(), "[dep](../roadmap/dep.md)\n")
        self.assertEqual(self.run_script("check").returncode, 0)

    def test_custom_locations(self):
        args = ("--index", "docs/roadmap.md", "--dir", "docs/roadmap")
        self.assertEqual(self.run_script("init", *args).returncode, 0)
        self.assertIn("[roadmap/](roadmap/)", (self.root / "docs/roadmap.md").read_text())
        self.write("docs/roadmap/item.md",
                   brief("title: Item\nstatus: done", "[Roadmap](../roadmap.md)\n"))
        result = self.run_script("archive", "item", *args)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[Roadmap](../../roadmap.md)",
                      (self.root / "docs/roadmap/archive/item.md").read_text())
        check = self.run_script("check", *args)
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_index_inside_brief_directory(self):
        args = ("--index", "tasks/roadmap.md", "--dir", "tasks")
        self.assertEqual(self.run_script("init", *args).returncode, 0)
        self.write("tasks/item.md", brief("title: Item\nstatus: planned", "[Roadmap](roadmap.md)\n"))
        self.assertEqual(self.run_script("sync", *args).returncode, 0)
        self.assertIn("[Item](item.md)", self.block("tasks/roadmap.md"))
        self.assertNotIn("[roadmap]", self.block("tasks/roadmap.md").lower().replace("roadmap:", ""))
        check = self.run_script("check", "--strict", *args)
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertNotIn("tasks/roadmap.md: no front matter", check.stdout)

        self.write("tasks/roadmap.md", "| # | Task |\n| --- | --- |\n")
        self.write("tasks/item.md", "# Item\n")
        self.assertEqual(self.run_script("check", *args).returncode, 2)

    def test_strict_adds_heuristic_warnings_only(self):
        self.init()
        self.write("roadmap/a.md", brief("title: A\nstatus: active",
                                         "Touches `src/missing.py`.\n"))
        self.run_script("sync")
        plain = self.run_script("check")
        self.assertEqual(plain.returncode, 0)
        self.assertNotIn("warning:", plain.stdout)
        strict = self.run_script("check", "--strict")
        self.assertEqual(strict.returncode, 0)
        for message in ("missing `summary`", "no `## Acceptance`", "no `## Progress`",
                        "no link back to the index", "`src/missing.py` does not exist"):
            self.assertIn(message, strict.stdout)


if __name__ == "__main__":
    unittest.main()
