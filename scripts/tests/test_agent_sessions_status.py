"""Read-only restoration reporting, using isolated records and mocked process state."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("agent_sessions_cli", REPO / "scripts/agent-sessions.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
ID_A = "0f6a2b4c-1d3e-4f50-8a9b-0c1d2e3f4a5b"
ID_B = "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"
NOW = 1_800_000_000
STARTED = "Tue Oct 27 19:00:00 2026"


class SessionStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
            "COPILOT_HOME": "", "CLAUDE_CONFIG_DIR": "", "NO_COLOR": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        cli.agent_sessions.session_name.cache_clear()
        self.addCleanup(cli.agent_sessions.session_name.cache_clear)
        self.directory = self.home / "state/agent-sessions"
        self.starts = {}
        self.number = 0

    def marker(self, *, session=ID_A, agent="copilot", launcher=None, age=0, claim="", pid="4242"):
        self.number += 1
        name = f"00000000-0000-4000-8000-{self.number:012d}"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (name + (f".{claim}" if claim else ""))
        path.write_text(f"{self.home}/project-{self.number}\n{launcher or agent}\n\n"
                        f"{pid}\n{STARTED}\n{session}\n{agent}\n")
        os.utime(path, (NOW - age,) * 2)
        return path

    def history(self, session=ID_A, agent="copilot", title=None):
        home = cli.agent_sessions.agent_home(agent)
        if agent == "copilot":
            directory = home / "session-state" / session
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "events.jsonl").touch()
            if title is not None:
                (directory / "workspace.yaml").write_text(f"name: {title}\n")
        else:
            path = home / "projects/-project" / f"{session}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"type": "custom-title", "customTitle": title}) + "\n"
                            if title is not None else "")

    def observe(self, marker, session, *, hook=False, age=0):
        path = (self.home / ".cache/agent-sessions" / marker.name.split(".")[0] if hook
                else marker.with_name(marker.name.split(".")[0] + ".current"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(session + "\n")
        os.utime(path, (NOW - age,) * 2)
        return path

    def run_report(self, *args):
        with patch.object(cli, "process_starts", return_value=self.starts), \
                patch.object(cli.time, "time", return_value=NOW), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            result = cli.main(list(args))
        return result, stdout.getvalue(), stderr.getvalue()

    def read(self, marker):
        return cli.read_session(marker, self.starts, NOW)

    def test_table_reports_titles_status_and_distinct_shells_without_writing(self):
        self.history(title="Configure Enonic sandbox")
        self.history(ID_B, "claude", "Release prep")
        self.marker()
        self.marker(session=ID_B, agent="claude", launcher="cplt", pid="5151")
        self.starts = {"4242": STARTED}
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.home.rglob("*") if p.is_file()}
        result, text, errors = self.run_report()
        self.assertEqual((result, errors), (0, ""))
        self.assertIn("~/project-1", text)
        self.assertIn("~/project-2", text)
        self.assertIn("Configure Enonic sandbox", text)
        self.assertIn("Release prep", text)
        self.assertIn(ID_A[:8], text)
        self.assertIn(ID_B[:8], text)
        self.assertIn("4242", text)
        self.assertIn("5151", text)
        self.assertIn("cplt/claude", text)
        self.assertIn("Live, tracked", text)
        self.assertIn("Waiting to restore", text)
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_live_shell_requires_the_recorded_start_identity_and_never_ages_out(self):
        self.history()
        path = self.marker(age=20 * 86400)
        self.starts = {"4242": STARTED}
        self.assertEqual(self.read(path).status, "Live, tracked")
        self.starts["4242"] = "Wed Oct 28 19:00:00 2026"
        self.assertEqual(self.read(path).status, "Expired")

    def test_sections_prioritise_live_then_waiting_then_other_and_keep_project_order(self):
        self.history()
        self.marker(pid="4242")
        self.marker(pid="5151")
        self.marker(pid="6161")
        self.marker(age=8 * 86400, pid="7171")
        self.starts = {"4242": STARTED}
        result, text, errors = self.run_report()
        self.assertEqual((result, errors), (0, ""))
        waiting = text.index("─ Waiting to restore ")
        live = text.index("─ Live sessions ")
        other = text.index("─ Other records ")
        self.assertLess(live, waiting)
        self.assertLess(waiting, other)
        self.assertLess(waiting, text.index("~/project-2"))
        self.assertLess(text.index("~/project-2"), text.index("~/project-3"))
        self.assertLess(text.index("~/project-3"), other)
        self.assertLess(live, text.index("~/project-1"))
        self.assertLess(text.index("~/project-1"), waiting)
        self.assertLess(other, text.index("~/project-4"))
        self.assertEqual(text.count("Project "), 3)
        for project in range(1, 5):
            self.assertEqual(text.count(f"~/project-{project}"), 1)

    def test_empty_sections_are_omitted(self):
        self.history()
        self.marker()
        self.starts = {"4242": STARTED}
        _, text, _ = self.run_report()
        self.assertIn("─ Live sessions ", text)
        self.assertNotIn("─ Waiting to restore ", text)
        self.assertNotIn("─ Other records ", text)
        self.assertEqual(text.count("Project "), 1)

    def test_table_displays_saved_app_and_accepts_legacy_records(self):
        self.history()
        for app, label in (("vscode", "VS Code"), ("iTerm.app", "iTerm"),
                           ("Apple_Terminal", "Terminal"), ("unknown", "unknown")):
            with self.subTest(app=app):
                marker = self.marker()
                marker.write_text(marker.read_text() + app + "\n")
                row = self.read(marker)
                self.assertEqual(row.app, app)
                with redirect_stdout(io.StringIO()) as stdout:
                    cli.report(self.directory, [row])
                self.assertIn(label, stdout.getvalue())
        self.assertEqual(self.read(self.marker()).app, "")

    def test_exact_seven_day_boundary_uses_observations_not_pid_snapshot(self):
        self.history()
        path = self.marker(age=8 * 86400)
        self.observe(path, ID_A, age=7 * 86400)
        self.assertEqual(self.read(path).status, "Expired")
        snapshot = path.with_name(path.name + ".pids")
        snapshot.write_text(f"{NOW}\n4242 1000000000\n")
        os.utime(snapshot, (NOW,) * 2)
        self.assertEqual(self.read(path).status, "Expired")
        self.observe(path, ID_A, hook=True, age=7 * 86400 - 1)
        self.assertEqual(self.read(path).status, "Waiting to restore")

    def test_latest_valid_observation_selects_both_id_and_title_with_current_winning_ties(self):
        self.history(title="First session")
        self.history(ID_B, title="Selected session")
        path = self.marker()
        self.observe(path, ID_B, age=1)
        self.observe(path, ID_A, hook=True, age=2)
        self.assertEqual((self.read(path).session, self.read(path).title), (ID_B, "Selected session"))
        self.observe(path, ID_A, hook=True, age=1)
        self.assertEqual(self.read(path).session, ID_B)
        self.observe(path, ID_A, hook=True)
        self.assertEqual((self.read(path).session, self.read(path).title), (ID_A, "First session"))
        self.observe(path, "invalid", hook=True)
        self.assertEqual(self.read(path).session, ID_B)

    def test_unknown_sessions_and_missing_history_are_not_reported_as_restorable(self):
        unknown = self.marker(session="")
        missing = self.marker(session=ID_B)
        self.assertEqual(self.read(unknown).status, "Session unknown")
        self.assertEqual(self.read(missing).status, "History missing")
        self.starts["4242"] = STARTED
        self.assertEqual(self.read(unknown).status, "Live, session unknown")
        self.assertEqual(self.read(missing).status, "Live, history missing")
        result, text, errors = self.run_report()
        self.assertEqual((result, errors), (0, ""))
        self.assertIn("(untitled)", text)
        self.assertNotIn("Live, tracked", text)
        self.assertNotIn("Waiting to restore", text)

    def test_claimed_records_are_not_presented_as_waiting(self):
        self.history()
        live = self.marker(claim="3131")
        stale = self.marker(claim="9191")
        self.starts["3131"] = STARTED
        self.assertEqual(self.read(live).status, "Restore claimed")
        self.assertEqual(self.read(stale).status, "Stale restore claim")
        with patch.object(cli, "process_starts", return_value=self.starts), \
                patch.object(cli.time, "time", return_value=NOW):
            self.assertEqual(len(cli.sessions(self.directory)), 2)

    def test_configured_agent_homes_supply_both_history_and_titles(self):
        with patch.dict(os.environ, {"COPILOT_HOME": str(self.home / "other-copilot"),
                                     "CLAUDE_CONFIG_DIR": str(self.home / "other-claude")}):
            self.history(title="Custom Copilot home")
            self.history(ID_B, "claude", "Custom Claude home")
            for session, agent, title in ((ID_A, "copilot", "Custom Copilot home"),
                                          (ID_B, "claude", "Custom Claude home")):
                row = self.read(self.marker(session=session, agent=agent))
                self.assertEqual((row.status, row.title), ("Waiting to restore", title))

    def test_empty_state_does_not_run_ps_or_create_a_directory(self):
        with patch.object(cli, "process_starts", side_effect=AssertionError("must not run ps")):
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(cli.main([]), 0)
        self.assertIn("No recorded sessions", stdout.getvalue())
        self.assertFalse(self.directory.exists())

    def test_help_and_invalid_arguments_do_not_inspect_or_modify_state(self):
        with patch.object(cli, "sessions", side_effect=AssertionError("must not inspect state")):
            for flag in ("--help", "-h"):
                result, text, errors = self.run_report(flag)
                self.assertEqual((result, errors), (0, ""))
                self.assertTrue(text.startswith("Usage: agent-sessions "))
            result, text, errors = self.run_report("--unknown")
            self.assertEqual(result, 2)
            self.assertEqual(text, "")
            self.assertIn("Error: unknown argument", errors)
        self.assertFalse(self.directory.exists())

    def test_process_query_failure_is_an_error_not_an_empty_report(self):
        self.marker()
        with patch.object(cli, "process_starts",
                          side_effect=subprocess.CalledProcessError(1, ["ps"])), \
                redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main([]), 1)
        self.assertIn("Error: could not read session restoration status", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_invalid_records_are_visible_and_fail_the_report(self):
        path = self.marker()
        for content in ("broken record\n", ""):
            with self.subTest(content=content):
                path.write_text(content)
                result, text, errors = self.run_report()
                self.assertEqual(result, 1)
                self.assertEqual(errors, "")
                self.assertIn("Invalid record", text)
                self.assertIn(path.name, text)

    def test_table_does_not_emit_control_characters_from_a_title(self):
        self.history(title='"Release\\tprep"')
        row = self.read(self.marker())
        row = row._replace(title="Release\tprep\x1b[31m")
        with redirect_stdout(io.StringIO()) as stdout:
            cli.report(self.directory, [row])
        self.assertNotIn("\t", stdout.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue())
        self.assertIn("Release prep", stdout.getvalue())
