"""Offline regression tests for codex-resume scheduling."""

from datetime import datetime, timedelta, timezone
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import fcntl
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/codex-resume.py"
SPEC = importlib.util.spec_from_file_location("codex_resume", SCRIPT)
codex_resume = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_resume)

REMOTE_HOST = "remote-control:env_e_mini"
CATALOG_COLUMNS = (
    "host_id TEXT, thread_id TEXT, display_title TEXT, cwd TEXT, thread_source TEXT, "
    "missing_candidate INTEGER, source_updated_at REAL, source_recency_at REAL"
)


def write_catalog(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE local_thread_catalog_hosts (host_id TEXT PRIMARY KEY, host_kind TEXT)")
    connection.executemany("INSERT INTO local_thread_catalog_hosts VALUES (?, ?)", [
        ("local", "local"), (REMOTE_HOST, "remote-control"), ("chatgpt:account", "chatgpt"),
    ])
    connection.execute(f"CREATE TABLE local_thread_catalog ({CATALOG_COLUMNS})")
    connection.executemany("INSERT INTO local_thread_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def seconds(text):
    return datetime.fromisoformat(text).timestamp()


def shown(moment):
    """Format a fixed moment as the script shows it, in the local time zone, which differs in CI."""
    return f"{datetime.fromisoformat(moment).astimezone():%d.%m.%Y kl. %H.%M}"


@contextmanager
def local_zone(name):
    """Set the system time zone, which the script uses for the offset of a future local time."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        time.tzset()


FAKE_APP_SERVER = """#!/usr/bin/env python3
import json, sys
mode = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "app-server" else "{mode}"
for line in sys.stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        experimental = request["params"].get("capabilities", {{}}).get("experimentalApi")
        print(json.dumps({{"id": 1, "result": {{}}}}), flush=True)
    elif request.get("id") == 2 and mode == "exit":
        sys.exit(0)
    elif request.get("id") == 2 and mode != "silent":
        if mode == "error":
            reply = {{"id": 2, "error": {{"message": "not signed in"}}}}
        else:
            reply = {{"id": 2, "result": {{"method": request["method"], "params": request.get("params"),
                                           "experimentalApi": experimental}}}}
        print("not json", flush=True)
        print(json.dumps(reply), flush=True)
"""
REAL_APP_SERVER_REQUEST = codex_resume.app_server_request
REAL_OPEN_APP = codex_resume.open_app
REAL_OPEN_CHAT = codex_resume.open_chat


class CodexResumeTests(unittest.TestCase):
    def setUp(self):
        # Never reach the real Codex CLI: a local chat would otherwise queue a real message.
        guard = patch.object(codex_resume, "app_server_request",
                             side_effect=AssertionError("unexpected Codex app server request"))
        guard.start()
        self.addCleanup(guard.stop)
        app = patch.object(codex_resume, "open_app")
        self.open_app = app.start()
        self.addCleanup(app.stop)
        chat = patch.object(codex_resume, "open_chat")
        self.open_chat = chat.start()
        self.addCleanup(chat.stop)
        # --status reads the recurring job, so keep this Mac's job out of the output.
        recurring = patch.object(codex_resume, "RECURRING_PLIST_PATH", Path("/nonexistent/recurring.plist"))
        recurring.start()
        self.addCleanup(recurring.stop)
    def test_requires_valid_24_hour_time(self):
        for value in (
            "4:15", "24:00", "12:60", "+5h", "12:30pm",
            "24.00", "2400", "15.60", "1560", "153", "15..03", "15:03\n",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as error:
                    codex_resume.next_occurrence(value)
                self.assertIn(f"invalid time {value!r}", str(error.exception))
                self.assertIn("15:03, 15.03, or 1503", str(error.exception))

    def test_accepts_colon_dot_and_compact_times_with_same_scheduling(self):
        now = datetime.fromisoformat("2026-10-16T13:00:00+02:00")
        for formats, expected in (
            (("15:03", "15.03", "1503"), "2026-10-16T15:03:00+02:00"),
            (("04:15", "04.15", "0415"), "2026-10-17T04:15:00+02:00"),
            (("00:00", "00.00", "0000"), "2026-10-17T00:00:00+02:00"),
            (("23:59", "23.59", "2359"), "2026-10-16T23:59:00+02:00"),
        ):
            for value in formats:
                with self.subTest(value=value), local_zone("Europe/Oslo"):
                    self.assertEqual(
                        codex_resume.next_occurrence(value, now),
                        datetime.fromisoformat(expected),
                    )

    def test_next_occurrence_uses_today_if_time_is_ahead(self):
        now = datetime.fromisoformat("2026-10-16T13:00:00+02:00")
        with local_zone("Europe/Oslo"):
            self.assertEqual(
                codex_resume.next_occurrence("16:30", now),
                datetime.fromisoformat("2026-10-16T16:30:00+02:00"),
            )

    def test_next_occurrence_moves_elapsed_time_to_tomorrow(self):
        now = datetime.fromisoformat("2026-10-16T16:30:00+02:00")
        with local_zone("Europe/Oslo"):
            self.assertEqual(
                codex_resume.next_occurrence("16:30", now),
                datetime.fromisoformat("2026-10-17T16:30:00+02:00"),
            )

    def test_next_occurrence_keeps_the_local_time_across_a_daylight_saving_change(self):
        with local_zone("Europe/Oslo"):
            for now, expected in (
                ("2026-10-24T23:00:00+02:00", "2026-10-25T09:00:00+01:00"),
                ("2026-03-28T23:00:00+01:00", "2026-03-29T09:00:00+02:00"),
            ):
                with self.subTest(now=now):
                    self.assertEqual(codex_resume.next_occurrence("09:00", datetime.fromisoformat(now)),
                                     datetime.fromisoformat(expected))

    def test_scheduled_wait_targets_three_seconds_after_requested_minute(self):
        target = "2026-10-16T16:30:00+02:00"
        now = datetime.fromisoformat("2026-10-16T16:30:01+02:00")
        with patch.object(codex_resume.time, "sleep") as sleep:
            codex_resume.wait_until_scheduled_time(target, now)
        sleep.assert_called_once_with(2)

    def test_scheduled_wait_does_not_sleep_if_launchd_runs_late(self):
        target = "2026-10-16T16:30:00+02:00"
        now = datetime.fromisoformat("2026-10-16T16:30:05+02:00")
        with patch.object(codex_resume.time, "sleep") as sleep:
            codex_resume.wait_until_scheduled_time(target, now)
        sleep.assert_not_called()

    def test_a_late_scheduled_run_says_how_late_it_is(self):
        planned = datetime.now().astimezone() - timedelta(hours=2, minutes=36, seconds=10)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(codex_resume, "PLIST_PATH", Path(temporary) / "job.plist"),
                patch.object(codex_resume, "log_stamp", return_value=""),
                patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                patch.object(codex_resume, "resume_through_queue") as queue,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(codex_resume.main(["--scheduled", planned.isoformat()]), 0)
        self.assertEqual(queue.call_args.args[:2], (self.LOCAL_CHAT, None))
        self.assertEqual(output.getvalue(),
                         "Running now (2 h 36 min late; the Mac was probably asleep or off).\n")
        self.assertEqual(codex_resume.late_note(planned, planned + timedelta(seconds=119)), "")
        self.assertEqual(codex_resume.late_note(planned, planned + timedelta(minutes=5)),
                         "5 min late; the Mac was probably asleep or off")

    def test_help_lists_supported_flags(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_code:
                codex_resume.main(["--help"])
        self.assertEqual(exit_code.exception.code, 0)
        for flag in ("--time", "--reset", "--limits", "--list", "--recurring", "--status", "--cancel", "--now",
                     "--message", "--chat", "--project", "--start-window"):
            self.assertIn(flag, output.getvalue())
        for hidden in ("--scheduled", "--recurring-check", "--every", "--used-up"):
            self.assertNotIn(hidden, output.getvalue())
        self.assertIn("goes through the Codex queue", output.getvalue())

    def test_actions_are_flags_and_must_be_selected_once(self):
        with self.assertRaises(SystemExit) as no_action:
            codex_resume.main([])
        self.assertEqual(no_action.exception.code, 2)
        with self.assertRaises(SystemExit) as multiple_actions:
            codex_resume.main(["--status", "--cancel"])
        self.assertEqual(multiple_actions.exception.code, 2)

    def test_schedule_starts_caffeinate_immediately_until_submission_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "com.hkjersem.codex-resume.plist"
            log_path = Path(temporary) / "codex-resume.log"
            at_half_past_four = datetime.fromisoformat("2026-10-16T16:30:00+02:00")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", log_path),
                patch.object(codex_resume, "launchctl") as launchctl,
                patch("builtins.print"),
            ):
                codex_resume.schedule(at_half_past_four)
                with plist_path.open("rb") as agent_file:
                    plist = plistlib.load(agent_file)

            self.assertEqual(plist["Label"], codex_resume.LABEL)
            self.assertTrue(plist["RunAtLoad"])
            self.assertNotIn("StartCalendarInterval", plist)
            # Only idle sleep is held off; the display may sleep and the screen lock.
            self.assertEqual(plist["ProgramArguments"][:2], ["/usr/bin/caffeinate", "-i"])
            self.assertEqual(plist["ProgramArguments"][2:4], [
                sys.executable, str(codex_resume.SCRIPT_PATH),
            ])
            self.assertEqual(plist["ProgramArguments"][-2:], [
                "--scheduled", "2026-10-16T16:30:00+02:00",
            ])
            launchctl.assert_any_call("bootstrap", codex_resume.agent_domain(), str(plist_path))

    def test_invalid_schedule_does_not_create_a_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "launchctl") as launchctl,
            ):
                with self.assertRaises(ValueError):
                    codex_resume.main(["--time", "25:00"])
            self.assertFalse(plist_path.exists())
            launchctl.assert_not_called()

    def test_cancel_unloads_before_removing_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "launchctl") as launchctl,
            ):
                self.assertTrue(codex_resume.remove_agent())
            launchctl.assert_called_once_with(
                "bootout", f"{codex_resume.agent_domain()}/{codex_resume.LABEL}"
            )
            self.assertFalse(plist_path.exists())

    def test_failed_launchctl_bootstrap_removes_plist(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            log_path = Path(temporary) / "codex-resume.log"
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", log_path),
                patch.object(codex_resume, "launchctl",
                             side_effect=RuntimeError("bootstrap failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                    codex_resume.schedule(datetime.fromisoformat("2026-10-16T16:30:00+02:00"))
            self.assertFalse(plist_path.exists())

    def test_scheduled_run_queues_to_the_latest_chat_and_unloads_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time") as wait,
                patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl") as launchctl,
            ):
                self.assertEqual(
                    codex_resume.main(["--scheduled", "2026-10-16T16:30:00+02:00"]),
                    0,
                )
            wait.assert_called_once_with("2026-10-16T16:30:00+02:00")
            queue.assert_called_once_with(self.LOCAL_CHAT, None, codex_resume.log_stamp)
            launchctl.assert_called_once_with(
                "bootout", f"{codex_resume.agent_domain()}/{codex_resume.LABEL}"
            )
            self.assertFalse(plist_path.exists())

    def test_scheduled_failure_is_logged_before_job_unloads_itself(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            stderr = io.StringIO()

            def bootout(*arguments):
                self.assertIn("Send failed", stderr.getvalue())
                raise SystemExit(0)  # launchd may terminate the calling process.

            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                patch.object(codex_resume, "resume_through_queue", side_effect=RuntimeError("Send failed")),
                patch.object(codex_resume, "launchctl", side_effect=bootout),
                redirect_stderr(stderr),
            ):
                with self.assertRaises(SystemExit):
                    codex_resume.main(["--scheduled", "2026-10-16T16:30:00+02:00"])
            self.assertFalse(plist_path.exists())

    def test_message_is_validated_and_queued_in_the_latest_chat(self):
        with (
            patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
            patch.object(codex_resume, "resume_through_queue") as queue,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--message", "  Resume if cut off  "]), 0)
        queue.assert_called_once_with(self.LOCAL_CHAT, "Resume if cut off")
        self.assertEqual(output.getvalue(), "Resuming in the latest chat, 'Find current project status'.\n")

    def test_invalid_messages_are_rejected_before_anything_is_queued(self):
        for value, fragment in (
            ("   ", "must not be empty"), ("one\ntwo", "single line"),
            ("tab\there", "single line"), ("/compact", "must not start with '/'"),
        ):
            with self.subTest(value=value):
                with patch.object(codex_resume, "resume_through_queue") as queue:
                    with self.assertRaisesRegex(ValueError, fragment):
                        codex_resume.main(["--now", "--message", value])
                queue.assert_not_called()

    def test_message_requires_time_or_now(self):
        for action in (["--status"], ["--cancel"], ["--list"], ["--limits"]):
            with self.subTest(action=action):
                with (
                    patch.object(codex_resume, "resume_through_queue") as queue,
                    redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as error:
                        codex_resume.main([*action, "--message", "resume"])
                self.assertEqual(error.exception.code, 2)
                queue.assert_not_called()

    def test_scheduled_message_is_stored_shown_and_used_when_it_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "com.hkjersem.codex-resume.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "next_occurrence",
                              return_value=datetime.fromisoformat("2026-10-16T16:30:00+02:00")),
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(output),
            ):
                codex_resume.main(["--time", "16:30", "--message", "Resume now"])
                codex_resume.main(["--status"])
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
            self.assertEqual(arguments[-4:], [
                "--message", "Resume now", "--scheduled", "2026-10-16T16:30:00+02:00",
            ])
            self.assertEqual(output.getvalue().splitlines(), [
                "Codex resume scheduled for 16.10.2026 kl. 16.30 in the latest chat on this Mac "
                "with message: Resume now",
                "",
                "─ Recurring check ──────────────────────────────────────────",
                "  Not set up. Start it with --recurring <minutes>.",
                "",
                "─ Scheduled resume ─────────────────────────────────────────",
                "  At          16.10.2026 kl. 16.30",
                "  Chat        latest chat on this Mac",
                "  Message     Resume now",
            ])

            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(queue.call_args.args[:2], (self.LOCAL_CHAT, "Resume now"))

    def chat_rows(self):
        return [
            ("local", "01a1-mine", "Find current project status", "/work/myntr", "user", 0,
             0, seconds("2026-09-04T13:56:00+02:00")),
            ("local", "01a2-new", "  Fresh\n  prompt  ", "/work/dash", None, 0,
             seconds("2026-10-18T09:00:00+02:00"), 0),
            ("local", "01a3-guard", "Guardian review", "/work", "guardian_review", 0,
             0, seconds("2026-10-19T11:29:00+02:00")),
            ("local", "01a4-old", "Find current project ideas", "/work/old", "user", 0,
             0, seconds("2026-08-01T10:00:00+02:00")),
            ("local", "01a5-gone", "Removed plan", "/work", "user", 1,
             0, seconds("2026-10-19T10:00:00+02:00")),
            ("local", "01a6-status", "Status board", "/work", "user", 0,
             0, seconds("2026-07-01T10:00:00+02:00")),
            (REMOTE_HOST, "01a7-mini", "Explore PWA roadmap", "/mini/local-relay", "user", 0,
             0, seconds("2026-10-10T08:00:00+02:00")),
            ("chatgpt:account", "6acf-web", "Plain ChatGPT chat", None, None, 0,
             0, seconds("2026-10-19T11:00:00+02:00")),
        ]

    def with_chats(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        write_catalog(home / codex_resume.CHAT_CATALOG, self.chat_rows())
        patcher = patch.object(codex_resume, "CODEX_HOME", home)
        patcher.start()
        self.addCleanup(patcher.stop)
        return home

    def test_chat_list_shows_this_macs_chats_newest_first(self):
        self.with_chats()
        chats = codex_resume.load_chats()
        # Chats on a Mac the app reaches remotely, and plain ChatGPT chats, are left out.
        self.assertEqual([chat["id"] for chat in chats], ["01a2-new", "01a1-mine", "01a4-old", "01a6-status"])
        self.assertEqual(chats[0]["name"], "Fresh prompt")
        self.assertEqual(codex_resume.latest_chat()["id"], "01a2-new")
        output = io.StringIO()
        with redirect_stdout(output):
            codex_resume.list_chats("status")
            codex_resume.list_chats("pwa")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[0], rf"^{shown('2026-09-04T13:56:00+02:00')}  01a1-mine  Find current project status\s+myntr$")
        self.assertRegex(lines[1], rf"^{shown('2026-07-01T10:00:00+02:00')}  01a6-status  Status board\s+work$")
        self.assertEqual(lines[2], "No matching chats.")
        self.assertNotIn("Guardian", output.getvalue())

    def test_chat_titles_set_since_the_app_started_are_used(self):
        home = self.with_chats()
        # The app's catalogue shows a title set through the app server only after a restart.
        connection = sqlite3.connect(home / codex_resume.CHAT_NAMES)
        connection.execute("CREATE TABLE threads (id TEXT, name TEXT)")
        connection.executemany("INSERT INTO threads VALUES (?, ?)",
                               [("01a1-mine", "!Find current project status"), ("01a2-new", "")])
        connection.commit()
        connection.close()
        names = {chat["id"]: chat["name"] for chat in codex_resume.load_chats()}
        self.assertEqual((names["01a1-mine"], names["01a2-new"]), ("!Find current project status", "Fresh prompt"))

    def test_missing_chat_catalog_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(codex_resume, "CODEX_HOME", Path(temporary)):
            with self.assertRaisesRegex(RuntimeError, "chat list .* was not found"):
                codex_resume.load_chats()

    def test_chat_is_resolved_by_id_prefix_or_name_and_never_guessed(self):
        self.with_chats()
        self.assertEqual(codex_resume.resolve_chat("01a4")["id"], "01a4-old")
        self.assertEqual(codex_resume.resolve_chat("01a2-new")["id"], "01a2-new")
        self.assertEqual(codex_resume.resolve_chat("status board")["id"], "01a6-status")
        for query, expected in (
            ("stat boa", "01a6-status"), ("board status", "01a6-status"),
            ("fre pro", "01a2-new"), ("pro fre", "01a2-new"),
        ):
            with self.subTest(query=query):
                self.assertEqual(codex_resume.resolve_chat(query)["id"], expected)
        with self.assertRaisesRegex(RuntimeError, "No chat matches"):
            codex_resume.resolve_chat("status zebra")
        with self.assertRaises(RuntimeError) as ambiguous:
            codex_resume.resolve_chat("find current")
        message = str(ambiguous.exception)
        self.assertIn("2 chats match", message)
        self.assertIn(f"{shown('2026-09-04T13:56:00+02:00')}  01a1-mine", message)
        self.assertIn(f"{shown('2026-08-01T10:00:00+02:00')}  01a4-old", message)
        for query in ("guardian", "removed", "plain chatgpt", "explore pwa", "nothing like this"):
            with self.subTest(query=query), self.assertRaisesRegex(RuntimeError, "No chat matches"):
                codex_resume.resolve_chat(query)

    def test_chat_is_resolved_now_and_stored_for_scheduled_runs(self):
        self.with_chats()
        with (
            patch.object(codex_resume, "resume_through_queue") as queue,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--chat", "01a1"]), 0)
        queue.assert_called_once()
        self.assertEqual(queue.call_args.args[0]["id"], "01a1-mine")
        self.assertEqual(output.getvalue(), "Resuming in chat 'Find current project status'.\n")
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(output),
            ):
                codex_resume.main(["--time", "16:30", "--chat", "status board"])
                codex_resume.main(["--status"])
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
            self.assertEqual(arguments[-4:-2], ["--chat", "01a6-status"])
            self.assertEqual(output.getvalue().count("in chat 'Status board'."), 1)
            self.assertIn("\n  Chat        'Status board'\n", output.getvalue())
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(queue.call_args.args[0]["name"], "Status board")

    def test_project_matches_folder_names_and_picks_its_latest_chat(self):
        self.with_chats()
        for query, chat_id in (("myntr", "01a1-mine"), ("DAS", "01a2-new"), ("/work", "01a6-status")):
            with self.subTest(query=query):
                self.assertEqual(codex_resume.resolve_project(query)[1]["id"], chat_id)
        with self.assertRaisesRegex(RuntimeError, "projects match 'o'") as error:
            codex_resume.resolve_project("o")
        self.assertIn("/work/old  'old'", str(error.exception))
        with self.assertRaisesRegex(RuntimeError, "No project matches 'local-relay'"):
            codex_resume.resolve_project("local-relay")

    def test_scheduled_project_uses_the_chat_that_is_latest_when_it_runs(self):
        home = self.with_chats()
        with (
            patch.object(codex_resume, "resume_through_queue") as queue,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--project", "myntr"]), 0)
        self.assertEqual(queue.call_args.args[0]["id"], "01a1-mine")
        self.assertEqual(output.getvalue(), "Resuming in chat 'Find current project status' in project 'myntr'.\n")
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(output),
            ):
                codex_resume.main(["--time", "16:30", "--project", "myntr"])
                codex_resume.main(["--status"])
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
            self.assertEqual(arguments[-4:-2], ["--project", "/work/myntr"])
            self.assertEqual(output.getvalue().count("in the latest chat in project 'myntr'."), 1)
            self.assertIn("\n  Chat        latest chat in project 'myntr'\n", output.getvalue())
            self.assertNotIn("in chat", output.getvalue())
            (home / codex_resume.CHAT_CATALOG).unlink()
            write_catalog(home / codex_resume.CHAT_CATALOG, self.chat_rows() + [
                ("local", "01a8-later", "Later myntr work", "/work/myntr", "user", 0,
                 0, seconds("2026-10-19T12:00:00+02:00")),
            ])
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(queue.call_args.args[0]["id"], "01a8-later")

    def test_scheduled_project_without_chats_fails_and_unloads(self):
        self.with_chats()
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_bytes(b"")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl") as launchctl,
                redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(codex_resume.main(
                    ["--project", "/work/deleted", "--scheduled", "2026-10-19T16:30:00+02:00"]), 1)
            queue.assert_not_called()
            launchctl.assert_called_once()
            self.assertFalse(plist_path.exists())
            self.assertIn("No project matches '/work/deleted'", errors.getvalue())

    def test_project_and_chat_cannot_be_combined_and_need_an_action(self):
        for arguments in (["--now", "--chat", "x", "--project", "y"], ["--status", "--project", "y"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_resume.main(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_chat_needs_an_action_that_uses_it(self):
        for arguments in (["--status", "--chat", "x"], ["--list", "--chat", "x"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_resume.main(arguments)
                self.assertEqual(error.exception.code, 2)

    def fake_cli(self, mode):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cli = Path(temporary.name) / "codex"
        cli.write_text(FAKE_APP_SERVER.format(mode=mode))
        cli.chmod(0o755)
        patcher = patch.object(codex_resume, "CODEX_CLI_PATHS", (cli,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_app_server_request_waits_for_the_reply_before_closing_input(self):
        self.fake_cli("answer")
        self.assertEqual(
            REAL_APP_SERVER_REQUEST("account/rateLimits/read"),
            {"method": "account/rateLimits/read", "params": None, "experimentalApi": True},
        )
        self.assertEqual(
            REAL_APP_SERVER_REQUEST("thread/queue/list", {"threadId": "01a1"}),
            {"method": "thread/queue/list", "params": {"threadId": "01a1"}, "experimentalApi": True},
        )

    def test_app_server_errors_and_silence_are_reported(self):
        self.fake_cli("error")
        with self.assertRaisesRegex(codex_resume.AppServerRejectedError,
                                    "rejected account/rateLimits/read: not signed in"):
            REAL_APP_SERVER_REQUEST("account/rateLimits/read")
        self.fake_cli("exit")
        with self.assertRaisesRegex(RuntimeError, "exited without answering") as error:
            REAL_APP_SERVER_REQUEST("account/rateLimits/read")
        self.assertNotIsInstance(error.exception, codex_resume.AppServerRejectedError)
        self.fake_cli("silent")
        with patch.object(codex_resume, "APP_SERVER_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(RuntimeError, "did not answer") as error:
                REAL_APP_SERVER_REQUEST("account/rateLimits/read")
        self.assertNotIsInstance(error.exception, codex_resume.AppServerRejectedError)
        with (
            patch.object(codex_resume, "CODEX_CLI_PATHS", ()),
            patch.object(codex_resume.shutil, "which", return_value=None),
            self.assertRaisesRegex(codex_resume.AppServerRejectedError, "Codex CLI is unavailable"),
        ):
            REAL_APP_SERVER_REQUEST("account/rateLimits/read")
        with (
            patch.object(codex_resume, "codex_cli", return_value="/nonexistent/codex"),
            self.assertRaisesRegex(codex_resume.AppServerRejectedError, "could not be started"),
        ):
            REAL_APP_SERVER_REQUEST("account/rateLimits/read")
        process = MagicMock()
        process.stdout = iter(())
        process.stdin.write.side_effect = BrokenPipeError(32, "Broken pipe")
        with (
            patch.object(codex_resume, "codex_cli", return_value="codex"),
            patch.object(codex_resume.subprocess, "Popen", return_value=process),
            self.assertRaisesRegex(RuntimeError, "stopped while sending thread/queue/add") as error,
        ):
            REAL_APP_SERVER_REQUEST("thread/queue/add", {"threadId": "01a1-mine"})
        self.assertNotIsInstance(error.exception, codex_resume.AppServerRejectedError)
        process.kill.assert_called_once()

    def fake_queue(self, listings, add_error=None, on_add=None, home=None):
        """Fake the app server's queue; each list request takes the next listing, repeating the last one."""
        calls = []

        def request(method, params=None):
            calls.append((method, params))
            if method == "thread/queue/add":
                if add_error is not None:
                    raise add_error
                if on_add is not None:
                    on_add()
                return {"queuedSubmission": {"id": "q1"}}
            if method == "thread/queue/list":
                listing = listings.pop(0) if len(listings) > 1 else listings[0]
                if isinstance(listing, Exception):
                    raise listing
                return listing
            raise AssertionError(f"unexpected {method}")

        if home is None:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            home = Path(temporary.name)
        # Sleeping moves a fake clock on, so waits for the app end at once instead of spinning in real time.
        clock = [0.0]
        for patcher in (
            patch.object(codex_resume, "app_server_request", side_effect=request),
            patch.object(codex_resume, "CODEX_HOME", home),
            patch.object(codex_resume, "REPLY_RESULT_SECONDS", 0),
            patch.object(codex_resume.time, "sleep",
                         side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
            patch.object(codex_resume.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def queued(self, *ids, cursor=None):
        return {"data": [{"id": item} for item in ids], "nextCursor": cursor}

    def write_session_log(self, *events, age=0, stamped=None):
        """Append reply events to the local chat's session log, as the app does, last written age seconds ago;
        the records are stamped at stamped, or now."""
        path = codex_resume.CODEX_HOME / "sessions/2026/10/20/rollout-2026-10-20T10-00-00-01a1-mine.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as log:
            log.write(json.dumps({"type": "response_item", "payload": {"type": "message"}}) + "\n")
            for event in events:
                payload = event if isinstance(event, dict) else {"type": event}
                moment = (stamped or datetime.now().astimezone()).astimezone(timezone.utc)
                log.write(json.dumps({"timestamp": f"{moment:%Y-%m-%dT%H:%M:%S.%f}"[:-3] + "Z",
                                      "type": "event_msg", "payload": payload}) + "\n")
        written = datetime.now().timestamp() - age
        os.utime(path, (written, written))

    LOCAL_CHAT = {"id": "01a1-mine", "name": "Find current project status", "cwd": "/work/myntr"}

    def limit_stop(self, turn):
        return {"type": "task_complete", "turn_id": turn,
                "error": {"message": "You've hit your usage limit.", "codex_error_info": "usage_limit_exceeded"}}

    def test_stuck_chat_is_one_whose_latest_replies_all_stopped_at_a_usage_limit(self):
        self.fake_queue([self.queued()])
        first, last = (datetime.fromisoformat(moment) for moment in ("2026-10-20T19:00:00+02:00",
                                                                     "2026-10-20T19:30:00+02:00"))
        self.write_session_log("task_started", "task_complete", stamped=first - timedelta(hours=1))
        self.write_session_log("task_started", self.limit_stop("t1"), stamped=first)
        self.assertEqual(codex_resume.stuck_at_limit(self.LOCAL_CHAT), {"closed": first, "stopped": first})
        # Messages sent while the limit is used up stop at it too; the chat stays stuck since the first stop.
        self.write_session_log("task_started", self.limit_stop("t2"), stamped=last)
        self.assertEqual(codex_resume.stuck_at_limit(self.LOCAL_CHAT), {"closed": first, "stopped": last})
        # After a resume, only later stops count: the resumed reply hitting the limit again is a new stop.
        resumed = first + timedelta(minutes=10)
        self.assertEqual(codex_resume.stuck_at_limit(self.LOCAL_CHAT, resumed), {"closed": last, "stopped": last})
        self.assertIsNone(codex_resume.stuck_at_limit(self.LOCAL_CHAT, last + timedelta(seconds=1)))
        self.write_session_log("task_started")
        self.assertIsNone(codex_resume.stuck_at_limit(self.LOCAL_CHAT))
        self.write_session_log("task_complete")
        self.assertIsNone(codex_resume.stuck_at_limit(self.LOCAL_CHAT))
        self.write_session_log("task_started", self.limit_stop("t3"), "turn_aborted")
        self.assertIsNone(codex_resume.stuck_at_limit(self.LOCAL_CHAT))
        self.write_session_log({"type": "task_complete", "error": {"codex_error_info": "server_error"}})
        self.assertIsNone(codex_resume.stuck_at_limit(self.LOCAL_CHAT))
        self.write_session_log(self.limit_stop("t4"), stamped=last)
        self.assertEqual(codex_resume.stuck_at_limit(self.LOCAL_CHAT), {"closed": last, "stopped": last})
        # A stamp without a time zone cannot be compared with the others, so it counts as unreadable.
        self.assertIsNone(codex_resume.record_time({"timestamp": "2026-10-20T20:00:00"}))
        self.assertFalse(codex_resume.written_since({"timestamp": "2026-10-20T20:00:00"}, first))
        self.assertEqual(codex_resume.record_time({"timestamp": "2026-10-20T18:00:00Z"}), first + timedelta(hours=1))
        self.assertIsNone(codex_resume.stuck_at_limit({**self.LOCAL_CHAT, "id": "01a1-unknown"}))

    def test_stuck_chats_are_recent_and_stopped_near_the_latest_limit_newest_first(self):
        now = datetime.fromisoformat("2026-10-27T10:00:00+02:00")
        closed = now - timedelta(days=6)
        chats = {
            "older": (closed, closed + timedelta(hours=2)),
            "latest": (closed + timedelta(hours=3), closed + timedelta(hours=3)),
            "edge": (closed - timedelta(hours=21), closed - timedelta(hours=21)),
            "earlier-limit": (closed - timedelta(hours=22), closed),
            "stale": (closed, closed + timedelta(hours=5)),
            "fine": None,
        }
        catalogue = [{"id": name, "name": name, "updated": now - timedelta(days=9 if name == "stale" else 5)}
                     for name in chats]

        def stuck(chat, since):
            self.assertIsNone(since)
            self.assertNotEqual(chat["id"], "stale")
            times = chats[chat["id"]]
            return times and {"run": chat["id"], "closed": times[0], "stopped": times[1]}

        with (patch.object(codex_resume, "load_chats", return_value=catalogue),
              patch.object(codex_resume, "stuck_at_limit", side_effect=stuck)):
            found = codex_resume.stuck_chats(now)
        self.assertEqual([chat["id"] for chat in found], ["latest", "older", "edge"])
        self.assertEqual(found[0]["name"], "latest")

    def test_status_lists_stuck_chats_with_what_the_next_check_does(self):
        now = datetime.fromisoformat("2026-10-22T20:00:00+02:00")
        limit = now - timedelta(hours=10)
        stops = {"a": limit + timedelta(hours=4), "b": limit + timedelta(hours=3), "c": limit + timedelta(hours=2),
                 "d": limit + timedelta(hours=1), "old": limit - timedelta(days=2)}
        catalogue = [{"id": name, "name": name, "cwd": "/work/app", "updated": now} for name in stops]
        # Until the app titles a chat, the catalogue holds its folder.
        catalogue[2]["name"] = "/work/local-relay"
        catalogue[2]["cwd"] = "/work/local-relay"

        def stuck(chat, since):
            return {"closed": stops[chat["id"]], "stopped": stops[chat["id"]]}

        # A resume in the current Codex window leaves room for two until its reported reset at 20.29.
        # Resumes from the previous window do not consume its slots.
        state = {"resumed": {"x": now - timedelta(hours=1), "y": now - timedelta(hours=6)}}
        state["sends"] = list(state["resumed"].values())

        def overview(limits):
            with (patch.object(codex_resume, "load_chats", return_value=catalogue),
                  patch.object(codex_resume, "stuck_at_limit", side_effect=stuck),
                  patch.object(codex_resume, "local_now", return_value=now),
                  patch.object(codex_resume, "usage_limits", **limits),
                  redirect_stdout(io.StringIO()) as output):
                codex_resume.print_stuck_overview(state)
            return output.getvalue().splitlines()

        t = {name: shown(stop.isoformat()) for name, stop in stops.items()}
        room = shown("2026-10-22T20:29:00+02:00")
        width = len(room)

        def table(a, b, c, d):
            return [
                "",
                "─ Chats stopped by a usage limit (5) ───────────────────────",
                f"  {'Stopped':<{width}}  {'Continues':<{width}}  Chat",
                f"  {t['a']}  {a:<{width}}  'a'",
                f"  {t['b']}  {b:<{width}}  'b'",
                f"  {t['c']}  {c}  untitled (local-relay, c)",
                f"  {t['d']}  {d}  'd'",
                f"  {t['old']}  {'left alone':<{width}}  'old'",
                "  Chats left alone were stopped by an older usage limit.",
            ]

        def limits(used):
            return {"return_value": [{"name": "5-hour", "minutes": 300, "used": used,
                                      "resets": datetime.fromisoformat("2026-10-22T20:29:00+02:00")}]}

        planned = table("next check", "next check", room, room)
        self.assertEqual(overview(limits(40)), planned)
        with self.assertRaisesRegex(RuntimeError, "app server is down"):
            overview({"side_effect": RuntimeError("app server is down")})
        # A used-up reset releases all three slots together, even though prior sends are less than five hours old.
        after_reset = shown("2026-10-22T20:30:00+02:00")
        self.assertEqual(overview(limits(100)), table(
            after_reset, after_reset, after_reset, shown("2026-10-23T01:30:00+02:00")))

    def test_status_while_a_limit_is_used_up_counts_the_windows_that_pass_marked_chats_by(self):
        now = datetime.fromisoformat("2026-10-22T20:00:00+02:00")
        chats = [{"id": name, "name": name, "earlier": False} for name in ("a", "!b")]
        window = {"start": now - timedelta(hours=2), "tier": 0, "early": False}

        def overview(resets):
            limits = [{"name": "5-hour", "minutes": 300, "used": 100, "resets": datetime.fromisoformat(resets)}]
            with (patch.object(codex_resume, "all_stuck_chats", return_value=chats),
                  patch.object(codex_resume, "usage_limits", return_value=limits)):
                found = codex_resume.stuck_overview(now, {}, [window["start"]], window)
            return {chat["id"]: at and f"{at:%d %H.%M}" for chat, _, at in found}

        # Removing !b's mark must not merge its tier with a's when the limit resets.
        self.assertEqual(overview("2026-10-22T23:59:00+02:00"), {"a": "23 00.00", "!b": "23 05.00"})
        # Two days from now is as far as it plans, however late the limit resets.
        self.assertEqual(overview("2026-10-25T19:59:00+02:00"), {"a": None, "!b": None})

    def test_status_keeps_passed_marked_chats_behind_unmarked_chats(self):
        now = datetime.fromisoformat("2026-10-28T11:04:00+01:00")
        first = datetime.fromisoformat("2026-10-28T08:09:00+01:00")
        later = datetime.fromisoformat("2026-10-28T09:08:00+01:00")
        chats = [{"id": name, "name": name, "earlier": False}
                 for name in ("!new", "!old", "a", "b", "c")]
        window = {"start": first, "tier": 0, "early": False}
        limits = [{"name": "5-hour", "minutes": 300, "used": 100,
                   "resets": datetime.fromisoformat("2026-10-28T13:08:00+01:00")}]
        for morning, last in ((None, "28 23.09"), ("09:00", "29 04.00")):
            with (self.subTest(morning=morning), local_zone("Europe/Oslo"),
                  patch.object(codex_resume, "all_stuck_chats", return_value=chats),
                  patch.object(codex_resume, "usage_limits", return_value=limits)):
                found = codex_resume.stuck_overview(
                    now, {}, [first, later, later], window, morning, passed={"!old": first})
            self.assertEqual({chat["id"]: f"{at:%d %H.%M}" for chat, _, at in found}, {
                "a": "28 13.09", "b": "28 13.09", "c": "28 13.09", "!new": "28 18.09", "!old": last,
            })

    def test_recurring_keeps_tiers_separate_after_removing_their_marks(self):
        state = {
            "resumed": {},
            "sends": ["2026-10-28T08:09:00+01:00", "2026-10-28T09:08:00+01:00",
                      "2026-10-28T09:08:00+01:00"],
            "window": {"start": "2026-10-28T08:09:00+01:00", "tier": 0, "early": False},
            "passed": {"new": "2026-10-28T08:09:00+01:00", "old": "2026-10-28T08:09:00+01:00"},
        }
        chats = [("new", "New"), ("old", "!Old"), ("a", "a"), ("b", "b"), ("c", "c")]
        for moment, expected in (("11:15", []), ("13:09", ["a", "b", "c"]), ("14:08", []),
                                 ("18:09", ["new"]), ("19:08", []), ("23:09", ["old"])):
            resumed, _, state = self.recurring(
                chats, state=state, now=f"2026-10-28T{moment}:00+01:00",
                stopped="2026-10-28T09:25:00+01:00")
            self.assertEqual(resumed, expected, moment)
            renamed = dict(self.renames)
            chats = [(chat, renamed.get(chat, title)) for chat, title in chats]

    def test_limit_reset_releases_all_three_resume_slots_together(self):
        now = datetime.fromisoformat("2026-10-28T13:09:00+01:00")
        previous = datetime.fromisoformat("2026-10-28T08:09:00+01:00")
        later = datetime.fromisoformat("2026-10-28T09:08:00+01:00")
        chats = [{"id": name, "name": name} for name in ("a", "b", "c")]
        window = {"start": previous, "tier": 0, "early": False}
        chosen, _ = codex_resume.plan_resumes(
            chats, now, [previous, later, later], window,
            limit_end=datetime.fromisoformat("2026-10-28T18:08:00+01:00"))
        self.assertEqual([chat["id"] for chat in chosen], ["a", "b", "c"])

    def test_recurring_uses_reported_reset_instead_of_each_sends_age(self):
        state = {
            "resumed": {},
            "sends": ["2026-10-28T08:09:00+01:00", "2026-10-28T09:08:00+01:00",
                      "2026-10-28T09:08:00+01:00"],
            "window": {"start": "2026-10-28T08:09:00+01:00", "tier": 0, "early": False},
        }
        resumed, _, state = self.recurring(
            ["a", "b", "c"], state=state, now="2026-10-28T13:00:00+01:00",
            stopped="2026-10-28T09:25:00+01:00", resets="2026-10-28T13:08:00+01:00",
            sent="2026-10-28T13:09:03+01:00", allowed=[True])
        self.assertEqual(resumed, ["a", "b", "c"])
        self.wait.assert_called_once_with("2026-10-28T13:09:00+01:00")
        # A new live reset, not the queueing time, is saved as the window's end.
        resumed, _, state = self.recurring(
            ["a", "b", "c", "d"], state=state, now="2026-10-28T13:15:00+01:00",
            stopped="2026-10-28T09:25:00+01:00", limit_end="2026-10-28T18:08:00+01:00")
        self.assertEqual(resumed, [])
        self.assertEqual(state["window"]["end"], "2026-10-28T18:08:00+01:00")
        resumed, _, _ = self.recurring(
            ["d"], state=state, now="2026-10-28T18:08:00+01:00",
            stopped="2026-10-28T09:25:00+01:00", limit_end="2026-10-28T23:08:00+01:00")
        self.assertEqual(resumed, ["d"])

    def test_recurring_reports_unreadable_or_missing_five_hour_limits(self):
        with patch.object(codex_resume, "five_hour_reset",
                          side_effect=RuntimeError("Codex reported no 5-hour usage window")):
            with self.assertRaisesRegex(RuntimeError, "no 5-hour usage window"):
                self.recurring(["a"])
        self.usage.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "no 5-hour usage window"):
            codex_resume.five_hour_reset([
                {"minutes": 10080, "used": 10, "resets": datetime.fromisoformat("2026-11-04T08:08:00+01:00")}])

    def recurring(self, chats, allowed=True, state=None, queued=True, now="2026-10-21T10:00:00+02:00",
                  stopped="2026-10-21T01:00:00+02:00", minutes=15, resets=None, sent=None, after_wait=None,
                  morning=None, rename_error=None, scan_error=None, limit_end=None):
        """Run one recurring check, set up every minutes, with chats stuck since stopped and a used-up limit
        resetting at resets; allowed is the answer, or the answers in turn, to whether the limits allow use.
        A wait ends at sent, with the chats in after_wait (default: the same) still stuck. Chats are named by
        their id, or given as (id, title); if given, reading them fails with scan_error and renaming one with
        rename_error, and self.renames records the new names.
        Return the chats it resumed, what it printed, and the state it saved."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state/recurring.json"
        if state is not None:
            state_path.parent.mkdir()
            state_path.write_text(state if isinstance(state, str) else json.dumps(state))
        self.state_path = state_path
        plist_path = Path(temporary.name) / "recurring.plist"
        if minutes is not None:
            plist_path.write_bytes(plistlib.dumps({"StartInterval": minutes * 60}))
        moment = datetime.fromisoformat(now)
        default_end = moment + timedelta(hours=5)
        if isinstance(state, dict) and state.get("window"):
            running = state["window"]
            end = datetime.fromisoformat(running.get("end", running["start"]))
            if "end" not in running:
                end += timedelta(hours=5)
            if end > moment:
                default_end = end
        windows = [{"name": "5-hour", "minutes": 300, "used": 100 if resets else 40,
                    "resets": datetime.fromisoformat(resets or limit_end) if resets or limit_end else default_end}]
        resumed = []

        def resume(chat, message, stamp):
            self.assertIsNone(message)
            # The resume is recorded before anything is queued, so a crash cannot send it twice.
            self.assertEqual(datetime.fromisoformat(json.loads(state_path.read_text())["resumed"][chat["id"]]),
                             datetime.fromisoformat(sent or now))
            resumed.append(chat["id"])
            outcome = queued.pop(0) if isinstance(queued, list) else queued
            if isinstance(outcome, Exception):
                raise outcome
            if outcome == "replying":
                return None
            if not outcome:
                raise codex_resume.NotQueuedError("Could not queue the message, so nothing was sent: rejected")
            return {"chat": chat}

        def stuck(moment, since):
            # As the real one does, stops before a chat's last resume do not count.
            if scan_error is not None:
                raise scan_error
            stop = datetime.fromisoformat(stopped)
            waited = after_wait is not None and wait.called
            found = [chat if isinstance(chat, tuple) else (chat, chat) for chat in (after_wait if waited else chats)]
            return [{**self.LOCAL_CHAT, "id": chat, "name": title, "stopped": stop}
                    for chat, title in found if chat not in since or since[chat] < stop]

        answers = {"side_effect": allowed} if isinstance(allowed, list) else {"return_value": allowed}
        self.renames = []

        def rename(method, params):
            self.assertEqual(method, "thread/name/set")
            if rename_error is not None:
                raise rename_error
            self.renames.append((params["threadId"], params["name"]))
            return {}

        with (patch.object(codex_resume, "RECURRING_STATE_PATH", state_path),
              patch.object(codex_resume, "RECURRING_PLIST_PATH", plist_path),
              patch.object(codex_resume, "stuck_chats", side_effect=stuck),
              patch.object(codex_resume, "usage_allowed", **answers) as usage,
              patch.object(codex_resume, "usage_limits", return_value=windows),
              patch.object(codex_resume, "wait_until_scheduled_time") as wait,
              patch.object(codex_resume, "local_now", return_value=datetime.fromisoformat(sent or now)),
              patch.object(codex_resume, "queue_message", side_effect=resume),
              patch.object(codex_resume, "watch_queued") as watch,
              patch.object(codex_resume, "app_server_request", side_effect=rename),
              patch.object(codex_resume, "log_stamp", return_value="[stamp] "),
              redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors):
            self.usage, self.wait, self.watch = usage, wait, watch
            try:
                codex_resume.recurring_check(datetime.fromisoformat(now), morning)
            finally:
                self.errors = errors.getvalue()
        return resumed, output.getvalue(), json.loads(state_path.read_text())

    def test_recurring_check_resumes_up_to_three_stuck_chats_once_per_stop(self):
        resumed, output, state = self.recurring(["a", "b", "c", "d"])
        self.assertEqual(resumed, ["a", "b", "c"])
        self.assertEqual(output.splitlines()[0], f"[stamp] Chat 'a' stopped at a usage limit "
                                                 f"{shown('2026-10-21T01:00:00+02:00')}; resuming it.")
        self.assertEqual(state["checked"], "2026-10-21T10:00:00+02:00")
        self.assertEqual(sorted(state["resumed"]), ["a", "b", "c"])
        # All three are queued before any is followed, so none waits for another's reply.
        self.watch.assert_called_once()
        self.assertEqual([entry["chat"]["id"] for entry in self.watch.call_args.args[0]], ["a", "b", "c"])
        # Within five hours the cap is spent; later, the next chat gets its turn, but never a resumed one again.
        self.assertEqual(self.recurring(["a", "d"], state=state, now="2026-10-21T14:59:00+02:00")[0], [])
        resumed, _, state = self.recurring(["a", "d"], state=state, now="2026-10-21T15:01:00+02:00")
        self.assertEqual(resumed, ["d"])
        self.assertEqual(self.recurring(["a", "b", "d"], state=state, now="2026-10-22T10:00:00+02:00")[0], [])
        # A limit stop after their resumes makes chats due again.
        self.assertEqual(self.recurring(["a", "b"], state=state, now="2026-10-22T10:00:00+02:00",
                                        stopped="2026-10-21T12:00:00+02:00")[0], ["a", "b"])

    def test_recurring_check_resumes_a_chat_again_after_its_resumed_reply_hits_the_limit(self):
        self.fake_queue([self.queued()])
        chat = {**self.LOCAL_CHAT, "updated": datetime.fromisoformat("2026-10-21T12:00:00+02:00")}
        self.write_session_log("task_started", self.limit_stop("t1"),
                               stamped=datetime.fromisoformat("2026-10-21T01:00:00+02:00"))
        state_path = codex_resume.CODEX_HOME / "recurring.json"
        sent = []
        with (patch.object(codex_resume, "RECURRING_STATE_PATH", state_path),
              patch.object(codex_resume, "load_chats", return_value=[chat]),
              patch.object(codex_resume, "usage_allowed", return_value=True),
              patch.object(codex_resume, "usage_limits", return_value=[
                  {"name": "5-hour", "minutes": 300, "used": 40,
                   "resets": datetime.fromisoformat("2026-10-21T15:00:00+02:00")}]),
              patch.object(codex_resume, "queue_message",
                           side_effect=lambda chat, *_, **__: sent.append(chat["id"])),
              redirect_stdout(io.StringIO())):
            codex_resume.recurring_check(datetime.fromisoformat("2026-10-21T10:00:00+02:00"))
            codex_resume.recurring_check(datetime.fromisoformat("2026-10-21T16:00:00+02:00"))
            self.assertEqual(sent, ["01a1-mine"])
            self.write_session_log("task_started", "token_count", self.limit_stop("t2"),
                                   stamped=datetime.fromisoformat("2026-10-21T11:00:00+02:00"))
            codex_resume.recurring_check(datetime.fromisoformat("2026-10-21T16:00:00+02:00"))
        self.assertEqual(sent, ["01a1-mine", "01a1-mine"])

    def test_recurring_check_stops_when_its_record_is_unreadable(self):
        for state in ("{\"resumed\": {\"a\": ", "[]", '{"resumed": {"a": "yesterday"}}',
                      '{"resumed": {"a": "2026-10-21T09:00:00"}}',
                      '{"resumed": {}, "tiers": {"a": {"tier": -1, "marks": 0}}}',
                      '{"resumed": {}, "tiers": {"a": {"tier": 1}}}'):
            with self.subTest(state=state):
                with self.assertRaisesRegex(RuntimeError, "recurring.json .*; fix or delete it"):
                    self.recurring(["a"], state=state)
                self.usage.assert_not_called()

    def test_recurring_check_skips_while_another_check_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "recurring.json"
            with (state_path.with_suffix(".lock").open("w") as lock,
                  patch.object(codex_resume, "RECURRING_STATE_PATH", state_path),
                  patch.object(codex_resume, "stuck_chats") as stuck):
                fcntl.flock(lock, fcntl.LOCK_EX)
                codex_resume.recurring_check()
                stuck.assert_not_called()
                fcntl.flock(lock, fcntl.LOCK_UN)
                stuck.return_value = []
                codex_resume.recurring_check()
                stuck.assert_called_once()

    def test_recurring_check_forgets_resumes_older_than_the_logs_it_scans(self):
        state = {"resumed": {"a": "2026-10-13T09:59:00+02:00", "b": "2026-10-13T10:00:00+02:00"}}
        resumed, _, state = self.recurring(["a", "b"], state=state, stopped="2026-10-13T08:00:00+02:00")
        self.assertEqual(resumed, ["a"])
        self.assertEqual(state["resumed"], {"b": "2026-10-13T10:00:00+02:00", "a": "2026-10-21T10:00:00+02:00"})

    def test_recurring_check_sends_nothing_while_a_limit_is_used_up(self):
        resumed, output, state = self.recurring(["a"], allowed=False)
        self.assertEqual((resumed, output, state["resumed"]), ([], "", {}))
        self.assertEqual(state["checked"], "2026-10-21T10:00:00+02:00")
        # The limits are only read when there is a chat to resume.
        self.recurring([])
        self.usage.assert_not_called()

    def test_recurring_check_waits_for_a_reset_before_its_next_check_and_sends_a_minute_after(self):
        # The resume is recorded when the wait actually ended, which can be late, as when the Mac slept.
        resumed, output, state = self.recurring(["a"], allowed=[False, True], resets="2026-10-21T10:10:00+02:00",
                                                sent="2026-10-21T16:00:00+02:00")
        self.assertEqual(datetime.fromisoformat(self.wait.call_args.args[0]),
                         datetime.fromisoformat("2026-10-21T10:11:00+02:00"))
        self.assertEqual(resumed, ["a"])
        self.assertEqual(datetime.fromisoformat(state["resumed"]["a"]),
                         datetime.fromisoformat("2026-10-21T16:00:00+02:00"))
        # Only chats still stuck after the wait are resumed, not one resumed meanwhile.
        resumed, _, _ = self.recurring(["a", "b"], allowed=[False, True], resets="2026-10-21T10:10:00+02:00",
                                       sent="2026-10-21T10:11:03+02:00", after_wait=["b"])
        self.assertEqual(resumed, ["b"])
        resumed, _, _ = self.recurring(["a"], allowed=[False], resets="2026-10-21T10:10:00+02:00",
                                       sent="2026-10-21T10:11:03+02:00", after_wait=[])
        self.assertEqual(resumed, [])
        self.assertEqual(output.splitlines()[0], "[stamp] 1 chat(s) stopped at a usage limit; waiting until "
                                                 f"{shown('2026-10-21T10:11:00+02:00')}, a minute after it resets.")
        # A reset at or after the next check, a limit still used up after the wait, or a used-up limit with no
        # reset time (or no recurring job to know the interval) is left to a later check.
        for resets, allowed, minutes in (("2026-10-21T10:14:00+02:00", [False, True], 15),
                                         ("2026-10-21T10:10:00+02:00", [False, False], 15),
                                         (None, [False, True], 15),
                                         ("2026-10-21T10:10:00+02:00", [False, True], None)):
            with self.subTest(resets=resets, allowed=allowed, minutes=minutes):
                resumed, _, state = self.recurring(["a"], allowed=allowed, resets=resets, minutes=minutes)
                self.assertEqual((resumed, state["resumed"]), ([], {}))
                self.assertEqual(self.wait.called, allowed == [False, False])

    def test_recurring_check_follows_the_chats_it_queued_before_the_app_server_failed(self):
        with self.assertRaisesRegex(RuntimeError, "app-server crashed"):
            self.recurring(["a", "b", "c"], queued=[True, RuntimeError("app-server crashed"), True])
        self.watch.assert_called_once()
        self.assertEqual([entry["chat"]["id"] for entry in self.watch.call_args.args[0]], ["a"])
        # The failed chat stays recorded, as its message may have been queued, and c is left to a later check.
        self.assertEqual(sorted(json.loads(self.state_path.read_text())["resumed"]), ["a", "b"])

    def test_recurring_cap_counts_each_resume_even_of_the_same_chat(self):
        # a was resumed twice in the last five hours, which its single time in resumed cannot show.
        state = {"resumed": {"a": "2026-10-21T09:00:00+02:00"},
                 "sends": ["2026-10-21T04:00:00+02:00", "2026-10-21T07:00:00+02:00", "2026-10-21T09:00:00+02:00"]}
        resumed, _, state = self.recurring(["a", "b", "c"], state=state, stopped="2026-10-21T09:30:00+02:00",
                                            limit_end="2026-10-21T12:00:00+02:00")
        self.assertEqual(resumed, ["a"])
        # Sends older than five hours are dropped.
        self.assertEqual(state["sends"], ["2026-10-21T07:00:00+02:00", "2026-10-21T09:00:00+02:00",
                                          "2026-10-21T10:00:00+02:00"])

    def test_recurring_check_retries_a_chat_when_nothing_was_queued(self):
        resumed, _, state = self.recurring(["a"], queued=False)
        self.assertEqual((resumed, state["resumed"], state["sends"]), (["a"], {}, []))
        self.assertEqual(self.errors, "[stamp] Could not queue the message, so nothing was sent: rejected\n")
        self.assertEqual(self.recurring(["a"], state=state)[0], ["a"])
        earlier = {"resumed": {"a": "2026-10-20T10:00:00+02:00"}}
        resumed, _, state = self.recurring(["a"], state=earlier, queued=False)
        self.assertEqual((resumed, state["resumed"]), (["a"], earlier["resumed"]))

    def test_recurring_check_keeps_the_resume_when_the_message_may_have_been_queued(self):
        uncertain = RuntimeError("The message may have been queued: no reply. Check the chat before retrying.")
        with self.assertRaisesRegex(RuntimeError, "may have been queued"):
            self.recurring(["a", "b"], queued=uncertain)
        state = json.loads(self.state_path.read_text())
        # Only the first chat was tried, and its record stays so it is not sent twice.
        self.assertEqual(state["resumed"], {"a": "2026-10-21T10:00:00+02:00"})
        self.assertEqual(self.recurring(["a"], state=state)[0], [])

    def test_marked_chats_sit_out_a_window_per_mark(self):
        # Unmarked chats get the first window, which removes one ! from each marked chat it passes by.
        resumed, _, state = self.recurring(["!b", "a", "!!c", "d"])
        self.assertEqual((resumed, self.renames), (["a", "d"], [("!b", "b"), ("!!c", "!c")]))
        self.assertEqual(state["window"], {"start": "2026-10-21T10:00:00+02:00", "tier": 0, "early": False,
                                           "end": "2026-10-21T15:00:00+02:00"})
        b, c = ("!b", "b"), ("!!c", "!c")
        # A marked chat that stops meanwhile is passed by too, and cannot join the window that passed it by,
        # though there is room; each window removes only one ! from a chat.
        resumed, _, state = self.recurring([b, c, "!g"], state=state, now="2026-10-21T10:15:00+02:00")
        self.assertEqual((resumed, self.renames), ([], [("!g", "g")]))
        g = ("!g", "g")
        # A check that cannot read the chats keeps the record of which window passed them by.
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            self.recurring([], state=state, now="2026-10-21T10:20:00+02:00", scan_error=RuntimeError("unreadable"))
        state = json.loads(self.state_path.read_text())
        resumed, _, state = self.recurring([b, c, g, "e"], state=state, now="2026-10-21T10:30:00+02:00")
        self.assertEqual((resumed, self.renames), (["e"], []))
        # The next window takes them as any other chats, newest first.
        resumed, _, state = self.recurring([b, g, c], state=state, now="2026-10-21T15:30:00+02:00")
        self.assertEqual((resumed, self.renames), (["!b", "!g"], [("!!c", "c")]))
        resumed, _, state = self.recurring([("!!c", "c")], state=state, now="2026-10-21T20:30:00+02:00")
        self.assertEqual((resumed, state["passed"]), (["!!c"], {}))
        # A failed rename is retried at the next check, so the chat does not sit out a window it was not counted
        # as passed by.
        resumed, _, state = self.recurring(["a", "!b"], rename_error=RuntimeError("app server is down"))
        self.assertEqual((resumed, state["passed"]), (["a"], {}))
        self.assertEqual(self.errors, "[stamp] Could not remove a ! from chat '!b': app server is down\n")
        # A failed rename is reported without stopping the run, and a window nothing was queued in never starts.
        resumed, _, _ = self.recurring(["!b"], rename_error=RuntimeError("app server is down"))
        self.assertEqual((resumed, self.watch.call_count), (["!b"], 1))
        self.assertEqual(self.errors, "[stamp] Could not remove the marks from chat '!b': app server is down\n")
        self.assertIsNone(self.recurring(["a"], queued=False)[2]["window"])
        # A chat Codex is already replying in gets nothing: it keeps its marks, and neither the cap nor a window
        # counts it, though its earlier stop is not resumed later.
        resumed, _, state = self.recurring(["!b"], queued="replying")
        self.assertEqual((resumed, self.renames, state["sends"], state["window"]), (["!b"], [], [], None))
        self.assertEqual(self.recurring(["!b"], state=state)[0], [])
        # A title of marks alone cannot be emptied, so that is reported.
        self.recurring(["!!"])
        self.assertEqual((self.renames, self.errors), ([], "[stamp] Could not remove the marks from chat '!!': its "
                                                            "title is only marks; rename it in the app\n"))

    def test_recurring_keeps_renamed_chats_behind_waiting_unmarked_chats(self):
        state = {
            "resumed": {},
            "sends": ["2026-10-21T08:09:00+02:00", "2026-10-21T09:08:00+02:00",
                      "2026-10-21T09:08:00+02:00"],
            "window": {"start": "2026-10-21T08:09:00+02:00", "tier": 0, "early": False},
            "passed": {},
        }
        resumed, _, state = self.recurring(["!new", "a", "b", "c"], state=state,
                                            now="2026-10-21T11:04:00+02:00")
        self.assertEqual((resumed, self.renames), ([], [("!new", "new")]))
        chats = [("!new", "new"), "a", "b", "c"]
        for moment, expected in (("13:09", ["a", "b", "c"]), ("14:08", []), ("18:09", ["!new"])):
            resumed, _, state = self.recurring(chats, state=state, now=f"2026-10-21T{moment}:00+02:00")
            self.assertEqual(resumed, expected)

    def test_recurring_only_admits_new_chats_of_the_running_windows_tier(self):
        _, _, state = self.recurring(["!b"])
        resumed, _, state = self.recurring(["a", "!c", "!!d"], state=state,
                                            now="2026-10-21T10:15:00+02:00")
        self.assertEqual(resumed, ["!c"])
        self.assertEqual(self.renames, [("!c", "c"), ("!!d", "!d")])
        resumed, _, state = self.recurring(["a", ("!!d", "!d")], state=state,
                                            now="2026-10-21T10:30:00+02:00")
        self.assertEqual(resumed, [])
        self.assertEqual(self.recurring(["a", ("!!d", "!d")], state=state,
                                        now="2026-10-21T15:00:00+02:00")[0], ["a"])

    def test_same_tier_chats_wait_for_the_limit_reset_when_capacity_is_full(self):
        state = {"resumed": {}, "sends": ["2026-10-21T10:00:00+02:00"]}
        resumed, _, state = self.recurring(["!a", "!b", "!c"], state=state)
        self.assertEqual(resumed, ["!a", "!b"])
        resumed, _, state = self.recurring(["!c"], state=state, now="2026-10-21T14:00:00+02:00")
        self.assertEqual(resumed, [])
        resumed, _, state = self.recurring(["!c"], state=state, now="2026-10-21T15:00:00+02:00")
        self.assertEqual(resumed, ["!c"])

    def test_manually_changing_marks_updates_a_retained_tier(self):
        _, _, state = self.recurring(["a", "!!b"])
        # The check removed one !, retaining tier 2. Removing the other by hand requests tier 0 instead.
        resumed, _, state = self.recurring([("!!b", "b"), "!c"], state=state,
                                            now="2026-10-21T10:15:00+02:00")
        self.assertEqual(resumed, [])
        resumed, _, state = self.recurring([("!!b", "b"), ("!c", "c")], state=state,
                                            now="2026-10-21T15:00:00+02:00")
        self.assertEqual(resumed, ["!!b"])
        # A later stop starts a fresh tier, not the one retained before its resume.
        self.assertEqual(self.recurring([("!!b", "b")], state=state,
                                        now="2026-10-21T15:15:00+02:00",
                                        stopped="2026-10-21T15:10:00+02:00")[0], ["!!b"])

    def test_at_night_chats_are_split_so_a_new_window_is_free_by_morning(self):
        with local_zone("Europe/Oslo"):
            night = {"morning": "09:00", "stopped": "2026-10-21T21:00:00+02:00"}
            # A single chat waits for the window five hours before morning, which may start up to an hour late.
            self.assertEqual(self.recurring(["a"], now="2026-10-21T23:30:00+02:00", **night)[0], [])
            self.assertEqual(self.recurring(["a"], now="2026-10-22T04:00:00+02:00", **night)[0], ["a"])
            self.assertEqual(self.recurring(["a"], now="2026-10-22T05:01:00+02:00", **night)[0], [])
            self.assertEqual(self.recurring(["a"], now="2026-10-22T09:00:00+02:00", **night)[0], ["a"])
            self.assertEqual(self.recurring(["a"], now="2026-10-21T22:59:00+02:00", **night)[0], ["a"])
            self.assertEqual(self.recurring(["a"], now="2026-10-21T23:30:00+02:00")[0], ["a"])
            # Two or more are split, the late window getting the larger half; the early one takes no later chats.
            resumed, _, state = self.recurring(["a", "b", "c"], now="2026-10-21T23:30:00+02:00", **night)
            self.assertEqual((resumed, state["window"]["early"]), (["a"], True))
            self.assertEqual(self.recurring(["b", "c", "d"], state=state, now="2026-10-22T01:00:00+02:00",
                                            **night)[0], [])
            self.assertEqual(self.recurring(["b", "c"], state=state, now="2026-10-22T04:30:00+02:00",
                                            **night)[0], ["b", "c"])

    def test_resume_times_follow_tiers_windows_and_nights(self):
        def times(*names, now="2026-10-21T23:00:00+02:00"):
            chats = [{"id": name, "name": name} for name in names]
            planned = codex_resume.resume_times(chats, datetime.fromisoformat(now), [], None, "09:00")
            return {chat: f"{at:%H.%M}" for chat, at in planned.items()}

        with local_zone("Europe/Oslo"):
            # A tier that needs two windows must finish before the next tier starts.
            self.assertEqual(times("a", "b", "c", "d", "!e", now="2026-10-21T12:00:00+02:00"),
                             {"a": "12.00", "b": "12.00", "c": "12.00", "d": "17.00", "!e": "22.00"})
            self.assertEqual(times("a"), {"a": "04.00"})
            self.assertEqual(times("a", "b", "c"), {"a": "23.00", "b": "04.00", "c": "04.00"})
            self.assertEqual(times("!a", "!!b"), {"!a": "23.00", "!!b": "04.00"})
            self.assertEqual(times("!a", "!b"), {"!a": "23.00", "!b": "04.00"})
            # An early window that starts late runs past the late one's start, so the rest wait for morning.
            self.assertEqual(times("a", "b", now="2026-10-22T01:00:00+02:00"), {"a": "01.00", "b": "09.00"})

    def test_usage_is_allowed_only_when_no_limit_is_used_up(self):
        for result, allowed in (
            ({"rateLimits": {"primary": {"usedPercent": 99}, "secondary": {"usedPercent": 40}}}, True),
            ({"rateLimits": {"primary": {"usedPercent": 0}}, "ordinaryUsageAllowed": True}, True),
            ({"rateLimits": {"primary": {"usedPercent": 100}, "secondary": {"usedPercent": 40}}}, False),
            ({"rateLimits": {"primary": {"usedPercent": 5}, "secondary": {"usedPercent": 100}}}, False),
            ({"rateLimits": {"primary": {"usedPercent": 5}}, "ordinaryUsageAllowed": False}, False),
        ):
            with self.subTest(result=result):
                with patch.object(codex_resume, "app_server_request", return_value=result) as request:
                    self.assertEqual(codex_resume.usage_allowed(), allowed)
                request.assert_called_once_with("account/rateLimits/read")

    def test_recurring_sets_up_and_stops_its_own_job_next_to_the_scheduled_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "recurring.plist"
            scheduled_path = Path(temporary) / "job.plist"
            scheduled_path.write_text("scheduled")
            state_path = Path(temporary) / "recurring.json"
            with (
                patch.object(codex_resume, "RECURRING_PLIST_PATH", plist_path),
                patch.object(codex_resume, "PLIST_PATH", scheduled_path),
                patch.object(codex_resume, "RECURRING_STATE_PATH", state_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "codex-resume.log"),
                patch.object(codex_resume, "launchctl") as launchctl,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(codex_resume.main(["--recurring", "15"]), 0)
                with plist_path.open("rb") as agent_file:
                    plist = plistlib.load(agent_file)
                state_path.write_text(json.dumps({"checked": "2026-10-21T10:00:00+02:00", "resumed": {}}))
                recurring_status = io.StringIO()
                with (redirect_stdout(recurring_status),
                      patch.object(codex_resume, "PLIST_PATH", Path(temporary) / "none.plist"),
                      patch.object(codex_resume, "stuck_overview", return_value=[]) as overview):
                    codex_resume.status()
                    state_path.write_text("{")
                    codex_resume.status()
                overview.assert_called_once()
                self.assertEqual(codex_resume.main(["--recurring", "off"]), 0)
                self.assertEqual(codex_resume.main(["--recurring", "off"]), 0)
                self.assertTrue(scheduled_path.exists())

            self.assertEqual(plist["Label"], codex_resume.RECURRING_LABEL)
            self.assertNotEqual(plist["Label"], codex_resume.LABEL)
            self.assertEqual(plist["StartInterval"], 15 * 60)
            self.assertTrue(plist["RunAtLoad"])
            self.assertEqual(plist["ProgramArguments"],
                             [sys.executable, str(codex_resume.SCRIPT_PATH), "--recurring-check"])
            self.assertFalse(plist_path.exists())
            self.assertEqual(launchctl.call_args_list, [
                call("bootstrap", codex_resume.agent_domain(), str(plist_path)),
                call("bootout", f"{codex_resume.agent_domain()}/{codex_resume.RECURRING_LABEL}"),
            ])
            lines = output.getvalue().splitlines()
            self.assertIn("Every 15 minutes", lines[0])
            self.assertEqual(lines[1:], ["Recurring check stopped.", "No recurring check is set up."])
            recurring = [
                "",
                "─ Recurring check ──────────────────────────────────────────",
                "  Every       15 minutes",
                "  Sends       continue to up to 3 stopped chats per Codex usage window, newest first, once the usage "
                "limits allow; one tier per window, with each leading ! placing a chat in a later tier",
            ]
            not_scheduled = ["", "─ Scheduled resume ─────────────────────────────────────────", "  None."]
            self.assertEqual(recurring_status.getvalue().splitlines(), [
                *recurring,
                f"  Last check  {shown('2026-10-21T10:00:00+02:00')}",
                "",
                "─ Chats stopped by a usage limit (0) ───────────────────────",
                "  None.",
                *not_scheduled,
                *recurring,
                f"  Stopped     checks stop until this is fixed: cannot read {state_path} "
                "(Expecting property name enclosed in double quotes: line 1 column 2 (char 1)); fix or delete it",
                *not_scheduled,
            ])

    def test_morning_is_stored_with_the_recurring_check_and_shown(self):
        with tempfile.TemporaryDirectory() as temporary, local_zone("Europe/Oslo"):
            plist_path = Path(temporary) / "recurring.plist"
            with (patch.object(codex_resume, "RECURRING_PLIST_PATH", plist_path),
                  patch.object(codex_resume, "RECURRING_STATE_PATH", Path(temporary) / "recurring.json"),
                  patch.object(codex_resume, "LOG_PATH", Path(temporary) / "codex-resume.log"),
                  patch.object(codex_resume, "PLIST_PATH", Path(temporary) / "none.plist"),
                  patch.object(codex_resume, "launchctl"),
                  patch.object(codex_resume, "recurring_check") as check,
                  redirect_stdout(io.StringIO()) as output):
                self.assertEqual(codex_resume.main(["--recurring", "15", "--morning", "09.00"]), 0)
                with plist_path.open("rb") as agent_file:
                    arguments = plistlib.load(agent_file)["ProgramArguments"]
                self.assertEqual(arguments[2:], ["--recurring-check", "--morning", "09:00"])
                self.assertEqual(codex_resume.main(arguments[2:]), 0)
                check.assert_called_once_with(morning="09:00")
                with patch.object(codex_resume, "stuck_overview", return_value=[]) as overview:
                    codex_resume.status()
                self.assertEqual(overview.call_args.args[4], "09:00")
            night = ("At night, from 23.00, waiting chats are split between an early window and one from 04.00, "
                     "and a single chat waits for that one, so a new window is free at about 09.00.")
            self.assertIn(night, output.getvalue().splitlines())
            self.assertIn(f"  Mornings    09:00. {night}", output.getvalue().splitlines())
        for arguments in (["--morning", "09:00", "--status"], ["--recurring", "off", "--morning", "09:00"],
                          ["--recurring", "15", "--morning", "9"]):
            with self.subTest(arguments=arguments):
                with (patch.object(codex_resume, "launchctl") as launchctl,
                      redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit)):
                    codex_resume.main(arguments)
                launchctl.assert_not_called()
                self.assertIn("--morning", errors.getvalue())

    def test_recurring_takes_off_or_at_least_five_minutes(self):
        for value in ("4", "0", "-5", "5.5", "never", ""):
            with self.subTest(value=value):
                with (patch.object(codex_resume, "launchctl") as launchctl,
                      redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit)):
                    codex_resume.main(["--recurring", value])
                launchctl.assert_not_called()
                self.assertIn("--recurring takes 'off' or a number of minutes, at least 5", errors.getvalue())

    def test_recurring_check_logs_errors_with_a_stamp(self):
        with (patch.object(codex_resume, "recurring_check", side_effect=RuntimeError("app server is down")),
              patch.object(codex_resume, "log_stamp", return_value="[stamp] "),
              redirect_stderr(io.StringIO()) as errors):
            self.assertEqual(codex_resume.main(["--recurring-check"]), 1)
        self.assertEqual(errors.getvalue(), "[stamp] Recurring check stopped: app server is down\n")

    def test_queue_sends_the_message_once_the_app_takes_it(self):
        calls = self.fake_queue([self.queued("q1"), self.queued("other", cursor="c2"), self.queued("q1"),
                                 self.queued("other")])
        with redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        add = calls[0]
        self.assertEqual(add[0], "thread/queue/add")
        self.assertEqual(add[1]["threadId"], "01a1-mine")
        self.assertEqual(add[1]["input"], [{"type": "text", "text": "continue", "text_elements": []}])
        self.assertTrue(add[1]["clientUserMessageId"])
        self.assertEqual([call[1].get("cursor") for call in calls[1:]], [None, None, "c2", None])
        self.assertEqual(output.getvalue().splitlines(), [
            "Queued the message in chat 'Find current project status'; waiting for the app to take it.",
            "The app took the message from the queue.",
            "Codex is replying.",
        ])
        # Only the app sends queued messages, so it is opened if it is not running.
        self.open_app.assert_called_once()
        # Taken at once, so the chat was loaded and is not opened.
        self.open_chat.assert_not_called()

    def test_queue_opens_the_chat_once_when_the_app_does_not_take_the_message(self):
        self.fake_queue([self.queued("q1"), self.queued("q1"), self.queued("q1"), self.queued()])
        with (patch.object(codex_resume, "QUEUE_OPEN_CHAT_SECONDS", 0), redirect_stdout(io.StringIO()) as output):
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        self.open_chat.assert_called_once()
        self.assertEqual(self.open_chat.call_args.args[0], self.LOCAL_CHAT)
        self.assertEqual(output.getvalue().splitlines()[-2], "The app took the message from the queue.")

    def test_queued_messages_are_followed_together_and_each_line_names_its_chat(self):
        listings = {"a": [True, False], "b": [True, True, False]}
        polled = []

        def still_queued(chat, item):
            polled.append(chat["id"])
            return listings[chat["id"]].pop(0)

        entries = self.entries("a", "b")
        replies = {"a": "The reply finished.", "b": None}
        with (patch.object(codex_resume, "still_queued", side_effect=still_queued),
              patch.object(codex_resume, "reply_result", side_effect=lambda entry: replies[entry["chat"]["id"]]),
              patch.object(codex_resume, "REPLY_RESULT_SECONDS", 0),
              patch.object(codex_resume.time, "sleep"),
              redirect_stdout(io.StringIO()) as output):
            codex_resume.watch_queued(entries)
        # Both are checked on every round until taken, rather than one after the other.
        self.assertEqual(polled, ["a", "b", "a", "b", "b"])
        self.assertEqual(output.getvalue().splitlines(), [
            "[t] 'a': The app took the message from the queue.",
            "[t] 'a': The reply finished.",
            "[t] 'b': The app took the message from the queue.",
            "[t] 'b': Codex is replying.",
        ])

    def watch(self, entries, still_queued, **patches):
        """Run watch_queued on a fake clock that sleeping moves on; return what it printed."""
        clock = [0.0]
        with ExitStack() as stack:
            for name, value in {"still_queued": MagicMock(side_effect=still_queued),
                                "reply_result": MagicMock(return_value="The reply finished."), **patches}.items():
                stack.enter_context(patch.object(codex_resume, name, value))
            stack.enter_context(patch.object(codex_resume.time, "sleep",
                                             side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
            stack.enter_context(patch.object(codex_resume.time, "monotonic", side_effect=lambda: clock[0]))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            try:
                codex_resume.watch_queued(entries)
            finally:
                self.watched = output.getvalue()

    def entries(self, *names):
        return [{"chat": {"id": name, "name": name}, "item": "q", "path": None, "offset": 0,
                 "queued_at": datetime.now().astimezone(), "stamp": lambda: "[t] "} for name in names]

    def test_watch_retries_unreadable_queues_and_reports_the_unknown_after_the_rest(self):
        down = RuntimeError("The Codex app server did not answer in time.")
        answers = {"a": [down, False]}

        def still_queued(chat, item):
            if chat["id"] == "b":
                raise down
            return answers["a"].pop(0)

        with self.assertRaisesRegex(RuntimeError, "^A message was queued in chat 'b', but whether the app took it "
                                                  "is unknown: The Codex app server did not answer in time. Check "
                                                  "the chat before retrying.$"):
            self.watch(self.entries("a", "b"), still_queued)
        self.assertEqual(self.watched.splitlines(), ["[t] 'a': The app took the message from the queue.",
                                                     "[t] 'a': The reply finished."])

    def test_watch_tries_opening_again_until_you_stop_using_the_app(self):
        opened = MagicMock(side_effect=[False, False, True])
        listings = [True] * 4 + [False]
        self.watch(self.entries("a"), lambda chat, item: listings.pop(0), open_chat=opened,
                   QUEUE_OPEN_CHAT_SECONDS=0)
        # Why it was not opened is logged once, and it is not opened again once it was.
        self.assertEqual([call.kwargs["announce"] for call in opened.call_args_list], [True, False, False])
        self.assertIn("The app took the message from the queue.", self.watched)

    def test_reply_result_waits_while_the_session_log_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(codex_resume, "chat_log", return_value=None):
            entry = {**self.entries("a")[0], "path": Path(temporary) / "compressed-meanwhile.jsonl"}
            self.assertIsNone(codex_resume.reply_result(entry))

    def test_chat_is_opened_in_the_background_unless_codex_is_in_use(self):
        def done(code=0, stderr=""):
            return subprocess.CompletedProcess([], code, "", stderr)

        for in_use, results, expected in (
            (True, [], "[t] The app has not taken the message yet and you are using it, so the chat was not "
                       "opened yet; the app sends the message once you open the chat.\n"),
            (False, [done()], "[t] The app has not taken the message yet; opened the chat in the background so "
                              "it loads it.\n"),
            (False, [done(code=1, stderr="LSOpenURLs failed\n")],
             "[t] Could not open the chat (LSOpenURLs failed); the app sends the message once you open it.\n"),
        ):
            with self.subTest(expected=expected):
                with (
                    patch.object(codex_resume, "codex_in_use", return_value=in_use),
                    patch.object(codex_resume.subprocess, "run", side_effect=results) as run,
                    redirect_stdout(io.StringIO()) as output,
                ):
                    REAL_OPEN_CHAT(self.LOCAL_CHAT, lambda: "[t] ")
                self.assertEqual(output.getvalue(), expected)
                self.assertEqual([call.args[0] for call in run.call_args_list],
                                 [["/usr/bin/open", "-g", "codex://threads/01a1-mine"]] * len(results))

    def test_codex_counts_as_in_use_only_when_frontmost_unlocked_and_recently_used(self):
        def probes(locked=False, idle=10, front="com.openai.codex", asn=b"ASN:0x0-0x2c02c:\n"):
            users = [{"kCGSSessionOnConsoleKey": True, **({"CGSSessionScreenIsLocked": True} if locked else {})}]
            outputs = {
                "Root": plistlib.dumps({"IOConsoleUsers": users}),
                "IOHIDSystem": plistlib.dumps([{"HIDIdleTime": int(idle * 1e9)}]),
                "front": asn,
                "info": f'"CFBundleIdentifier"="{front}"\n'.encode(),
            }
            return lambda *command: next(value for key, value in outputs.items() if key in command)

        for case, fake, expected in (
            ("frontmost, unlocked and used", probes(), True),
            ("screen locked", probes(locked=True), False),
            ("idle five minutes", probes(idle=300), False),
            ("another app frontmost", probes(front="com.microsoft.VSCode"), False),
            ("no frontmost app read", probes(asn=b""), True),
            ("state unreadable", MagicMock(side_effect=subprocess.CalledProcessError(1, "ioreg")), True),
        ):
            with self.subTest(case), patch.object(codex_resume, "probe", side_effect=fake):
                self.assertEqual(codex_resume.codex_in_use(), expected)

    def test_queue_reports_how_the_reply_it_started_ended(self):
        limit = {"type": "task_complete", "error": {"message": "You've hit your usage limit."}}
        for events, expected in (
            ((limit,), "The reply ended with an error: You've hit your usage limit."),
            (("task_complete",), "The reply finished."),
            (("turn_aborted",), "The reply was stopped."),
            ((), "Codex is replying."),
        ):
            with self.subTest(expected=expected):
                self.fake_queue([self.queued()], on_add=lambda: self.write_session_log("task_started", *events))
                # An earlier reply's end must not be mistaken for this one's.
                self.write_session_log("task_started", "task_complete")
                with redirect_stdout(io.StringIO()) as output:
                    self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
                self.assertEqual(output.getvalue().splitlines()[-1], expected)

    def test_queue_reports_only_the_new_reply_when_the_log_was_compressed(self):
        home = self.with_chats()
        compressed = home / "sessions/2026/10/20/rollout-2026-10-20T10-00-00-01a1-mine.jsonl.zst"
        compressed.parent.mkdir(parents=True)
        compressed.write_bytes(b"compressed")

        def unpack_and_reply():
            # The app unpacks the log, with its earlier replies, before writing the new one.
            compressed.unlink()
            earlier = datetime.now().astimezone() - timedelta(days=30)
            self.write_session_log("task_started", "task_complete", stamped=earlier)
            self.write_session_log("task_started", {"type": "task_complete", "error": {"message": "Usage limit."}})

        self.fake_queue([self.queued()], on_add=unpack_and_reply, home=home)
        with redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        self.assertEqual(output.getvalue().splitlines()[-1], "The reply ended with an error: Usage limit.")
        self.assertFalse(codex_resume.written_since({"timestamp": "unreadable"}, datetime.now().astimezone()))
        self.assertFalse(codex_resume.written_since({}, datetime.now().astimezone()))

    def test_queue_leaves_a_reply_in_progress_alone(self):
        calls = self.fake_queue([self.queued("q1")])
        self.write_session_log("task_started", "task_complete", "task_started")
        with redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, None, lambda: "[t] "))
        self.assertEqual(calls, [])
        self.assertEqual(output.getvalue(),
                         "[t] Codex is already replying in chat 'Find current project status'; not sending 'continue'.\n")
        with redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, "After this"))
        self.assertEqual([call[0] for call in calls], ["thread/queue/add"])
        self.assertIn("queued the message, and the app sends it when the reply ends", output.getvalue())
        # A crashed app can leave a reply looking unfinished, so a queued message still opens the app.
        self.open_app.assert_called_once()

    def test_reply_in_a_reverted_chats_new_log_counts_as_in_progress(self):
        self.fake_queue([self.queued()])
        self.write_session_log("task_started", "task_complete", age=3600)
        reverted = codex_resume.CODEX_HOME / "sessions/2026/10/20/rollout-2026-10-20T11-00-00-01a1-mine_01a2-new.jsonl"
        reverted.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
        other = codex_resume.CODEX_HOME / "sessions/2026/10/20/rollout-2026-10-20T11-00-00-01a1-mine2.jsonl"
        other.write_text("")
        self.assertEqual(codex_resume.chat_log(self.LOCAL_CHAT), reverted)
        self.assertTrue(codex_resume.replying(self.LOCAL_CHAT))

    def test_reply_counts_as_in_progress_only_when_started_unended_and_recent(self):
        self.fake_queue([self.queued()])
        self.assertFalse(codex_resume.replying(self.LOCAL_CHAT))
        for events, age, expected in (
            (("task_started",), 0, True),
            (("task_started", "task_complete"), 0, False),
            (("task_started", "turn_aborted"), 0, False),
            (("task_started",), codex_resume.REPLY_STALE_SECONDS + 60, False),
        ):
            with self.subTest(events=events, age=age):
                self.fake_queue([self.queued()])
                self.write_session_log(*events, age=age)
                self.assertEqual(codex_resume.replying(self.LOCAL_CHAT), expected)

    def test_queue_message_left_untaken_stays_queued(self):
        calls = self.fake_queue([self.queued("q1")])
        with (
            patch.object(codex_resume, "QUEUE_PICKUP_SECONDS", 0),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertIsNone(codex_resume.resume_through_queue(self.LOCAL_CHAT, "Resume now", lambda: "[t] "))
        self.assertEqual(calls[0][1]["input"][0]["text"], "Resume now")
        self.assertEqual([call[0] for call in calls], ["thread/queue/add", "thread/queue/list"])
        self.assertEqual(output.getvalue().splitlines()[-1],
                         "[t] The app has not taken the message within 0 minutes; it stays queued, "
                         "and the app sends it once the chat is open.")

    def test_app_is_opened_in_the_background_only_when_it_is_not_running(self):
        def done(stdout="", code=0, stderr=""):
            return subprocess.CompletedProcess([], code, stdout, stderr)

        running = 'ASN:0x0-0x1b59b58-"ChatGPT":\n'
        for results, expected in (
            ([done(running)], ""),
            ([done(), done()], "[t] Opened the Codex app in the background.\n"),
            ([done(), done(code=1, stderr="LSOpenURLs failed\n")],
             "[t] Could not open the Codex app (LSOpenURLs failed); it sends the message once it runs.\n"),
            ([OSError("no lsappinfo")],
             "[t] Could not open the Codex app (no lsappinfo); it sends the message once it runs.\n"),
        ):
            with self.subTest(expected=expected):
                with (
                    patch.object(codex_resume.subprocess, "run", side_effect=results) as run,
                    redirect_stdout(io.StringIO()) as output,
                ):
                    REAL_OPEN_APP(lambda: "[t] ")
                self.assertEqual(output.getvalue(), expected)
                self.assertEqual(run.call_args_list[0].args[0],
                                 ["/usr/bin/lsappinfo", "find", "bundleid=com.openai.codex"])
                if len(run.call_args_list) > 1:
                    self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/open", "-g", "-b", "com.openai.codex"])

    def test_queue_rejection_and_uncertainty_are_reported(self):
        calls = self.fake_queue([self.queued()], add_error=codex_resume.AppServerRejectedError("thread not found"))
        with redirect_stdout(io.StringIO()) as output, self.assertRaisesRegex(
                codex_resume.NotQueuedError, "Could not queue the message, so nothing was sent: thread not found"):
            codex_resume.resume_through_queue(self.LOCAL_CHAT, "Resume now")
        self.assertEqual([call[0] for call in calls], ["thread/queue/add"])
        self.assertEqual(output.getvalue(), "")
        self.open_app.assert_not_called()
        timeout = RuntimeError("The Codex app server did not answer in time.")
        for kind, setup in (
            ("may have been queued", {"listings": [self.queued()], "add_error": timeout}),
            ("whether the app took it is unknown", {"listings": [timeout]}),
        ):
            for message in (None, "Resume now"):
                with self.subTest(kind=kind, message=message):
                    self.fake_queue(**setup)
                    with redirect_stdout(io.StringIO()), \
                            self.assertRaisesRegex(RuntimeError, f"{kind}.*Check the chat before retrying") as error:
                        codex_resume.resume_through_queue(self.LOCAL_CHAT, message)
                    self.assertNotIsInstance(error.exception, codex_resume.NotQueuedError)

    def test_scheduled_queue_uncertainty_is_logged_and_unloads(self):
        timeout = RuntimeError("The Codex app server did not answer in time.")
        for message in (None, "Resume now"):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                self.fake_queue([timeout], home=self.with_chats())
                plist_path = Path(temporary) / "job.plist"
                plist_path.write_text("scheduled")
                with (
                    patch.object(codex_resume, "PLIST_PATH", plist_path),
                    patch.object(codex_resume, "wait_until_scheduled_time"),
                    patch.object(codex_resume, "launchctl") as launchctl,
                    redirect_stdout(io.StringIO()) as log,
                    redirect_stderr(io.StringIO()) as errors,
                ):
                    self.assertEqual(codex_resume.main(
                        [*(["--message", message] if message else []), "--chat", "01a1-mine",
                         "--scheduled", datetime.now().astimezone().isoformat()]), 1)
                launchctl.assert_called_once()
                self.assertFalse(plist_path.exists())
                self.assertRegex(log.getvalue(), r"^\[\d\d\.\d\d\.\d{4} kl\. \d\d\.\d\d\.\d\d\] Queued the message")
                self.assertIn("whether the app took it is unknown", errors.getvalue())

    def limits(self, primary_used, secondary_used):
        return {"rateLimits": {
            "primary": {"usedPercent": primary_used, "windowDurationMins": 300,
                        "resetsAt": int(datetime.fromisoformat("2026-10-19T11:51:00+02:00").timestamp())},
            "secondary": {"usedPercent": secondary_used, "windowDurationMins": 10080,
                          "resetsAt": int(datetime.fromisoformat("2026-10-25T02:55:00+02:00").timestamp())},
        }}

    def test_reset_waits_for_the_latest_used_up_limit(self):
        five_hour = datetime.fromisoformat("2026-10-19T11:52:00+02:00")
        weekly = datetime.fromisoformat("2026-10-25T02:56:00+02:00")
        for used, expected in (((100, 76), five_hour), ((40, 100), weekly), ((100, 100), weekly)):
            with self.subTest(used=used):
                with patch.object(codex_resume, "app_server_request", return_value=self.limits(*used)):
                    windows = codex_resume.usage_limits()
                self.assertEqual(codex_resume.reset_time(windows), expected)
        with patch.object(codex_resume, "app_server_request", return_value=self.limits(98, 76)):
            windows = codex_resume.usage_limits()
        with self.assertRaisesRegex(RuntimeError, "No Codex usage limit is used up") as error:
            codex_resume.reset_time(windows)
        self.assertIn("5-hour: 98% used, resets ", str(error.exception))
        self.assertIn(
            f"weekly: 76% used, resets {datetime.fromisoformat('2026-10-25T02:55:00+02:00').astimezone():%d.%m.%Y kl. %H.%M}",
            str(error.exception))

    def test_reset_schedules_the_job_with_message_and_chat(self):
        self.with_chats()
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "launchctl"),
                patch.object(codex_resume, "app_server_request", return_value=self.limits(100, 76)),
                redirect_stdout(output),
            ):
                self.assertEqual(codex_resume.main(
                    ["--reset", "--message", "Resume now", "--chat", "01a1"]), 0)
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
        scheduled = datetime.fromisoformat(arguments[-1])
        self.assertEqual(scheduled - datetime.fromisoformat("2026-10-19T11:51:00+02:00"), timedelta(minutes=1))
        self.assertEqual(arguments[4:-2], ["--message", "Resume now", "--chat", "01a1-mine"])
        self.assertIn("5-hour: 100% used", output.getvalue())

    def test_recurring_reset_is_stored_shown_and_needs_five_minutes(self):
        self.with_chats()
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "launchctl"),
                patch.object(codex_resume, "app_server_request", return_value=self.limits(100, 76)),
                redirect_stdout(output),
            ):
                self.assertEqual(codex_resume.main(["--reset", "30", "--chat", "01a1"]), 0)
                codex_resume.main(["--status"])
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
        self.assertEqual(arguments[4:-2], ["--chat", "01a1-mine", "--every", "30", "--used-up", "300"])
        lines = output.getvalue().splitlines()
        self.assertRegex(lines[lines.index("─ Recurring check ──────────────────────────────────────────") - 2], r"^Codex resume scheduled for \S+ kl\. \S+, or earlier if the limit resets "
                                   r"first \(checked every 30 minutes\) in chat 'Find current project status'\.$")
        self.assertRegex(lines[-3], r"^  At          \S+ kl\. \S+, or earlier if the limit resets first "
                                    r"\(checked every 30 minutes\)$")
        self.assertEqual(lines[-2:], [
            "  Chat        'Find current project status'",
            "  Message     continue (not sent if Codex is already replying)",
        ])
        for minutes in ("4", "0x"):
            with self.subTest(minutes=minutes), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_resume.main(["--reset", minutes])
                self.assertEqual(error.exception.code, 2)

    RESETS_AT = "2026-10-20T14:25:00+02:00"
    SHOWN_RESET = shown(RESETS_AT)

    def reset_limits(self, five_hour, weekly=40, allowed=True):
        resets = int(datetime.fromisoformat(self.RESETS_AT).timestamp())
        return {"ordinaryUsageAllowed": allowed, "rateLimits": {
            "primary": {"usedPercent": five_hour, "windowDurationMins": 300, "resetsAt": resets},
            "secondary": {"usedPercent": weekly, "windowDurationMins": 10080, "resetsAt": None},
        }}

    def run_recurring_reset(self, readings, minutes_to_reset, used_up="300", asleep_minutes=0):
        """Run a scheduled --reset 30 job on a fake clock that advances while sleeping; the first wait
        overruns by asleep_minutes, as when the Mac sleeps."""
        clock = [datetime.fromisoformat("2026-10-20T12:00:00+02:00")]
        deadline = clock[0] + timedelta(minutes=minutes_to_reset)

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0].astimezone(tz)

        overrun = [timedelta(minutes=asleep_minutes)]

        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds) + (overrun.pop() if overrun else timedelta())

        checks = []

        def read(method, params=None):
            self.assertEqual(method, "account/rateLimits/read")
            checks.append(clock[0])
            reading = readings.pop(0)
            if isinstance(reading, Exception):
                raise reading
            return reading

        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "datetime", FakeDatetime),
                patch.object(codex_resume.time, "sleep", side_effect=sleep),
                patch.object(codex_resume, "app_server_request", side_effect=read),
                patch.object(codex_resume, "log_stamp", return_value=""),
                patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                patch.object(codex_resume, "resume_through_queue") as queue,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(codex_resume.main(
                    ["--every", "30", "--used-up", used_up, "--scheduled", deadline.isoformat()]), 0)
        self.assertEqual(readings, [])
        return ([(moment - deadline).total_seconds() / 60 for moment in checks], queue.call_count,
                output.getvalue().splitlines())

    def test_recurring_reset_sends_once_an_early_reset_leaves_the_limit_unused(self):
        checks, sends, lines = self.run_recurring_reset(
            [self.reset_limits(100), RuntimeError("no answer"), self.reset_limits(0)], minutes_to_reset=200)
        self.assertEqual(checks, [-170, -140, -110])
        self.assertEqual(sends, 1)
        limits = "5-hour: {}% used, resets " + self.SHOWN_RESET + "; weekly: 40% used, resets not set"
        self.assertEqual(lines, [
            f"Not sending yet ({limits.format(100)}); next check at 20.10.2026 kl. 13.00.",
            "Not sending yet (the limits could not be read: no answer); next check at 20.10.2026 kl. 13.30.",
            f"The limit has reset early and its new allowance is unused ({limits.format(0)}); sending.",
        ])

    def test_recurring_reset_stops_when_the_new_allowance_is_already_in_use(self):
        for final in (False, True):
            with self.subTest(final=final):
                readings = [self.reset_limits(0, allowed=False), self.reset_limits(100), self.reset_limits(12)]
                checks, sends, lines = self.run_recurring_reset(readings, minutes_to_reset=70 if final else 200)
                self.assertEqual(checks, [-40, -10, 0.05] if final else [-170, -140, -110])
                self.assertEqual(sends, 0)
                self.assertIn("usage not allowed", lines[0])
                self.assertRegex(lines[-1], r"^The limit has reset, but its new allowance is already in use "
                                            r"\(5-hour: 12% used.*\); not sending\.$")

    def test_recurring_reset_says_when_a_check_ran_late(self):
        checks, sends, lines = self.run_recurring_reset(
            [self.reset_limits(12)], minutes_to_reset=70, asleep_minutes=150)
        self.assertEqual(checks, [110])
        self.assertEqual(sends, 0)
        self.assertRegex(lines[-1], r"already in use \(5-hour: 12% used.*; "
                                    r"2 h 30 min late; the Mac was probably asleep or off\); not sending\.$")

    def test_recurring_reset_judges_only_the_limits_that_were_used_up(self):
        # The weekly limit ran out mid-window; the 5-hour window's earlier use is not a new session.
        checks, sends, lines = self.run_recurring_reset(
            [self.reset_limits(30, weekly=100), self.reset_limits(30, weekly=0)],
            minutes_to_reset=200, used_up="10080")
        self.assertEqual(sends, 1)
        self.assertIn("reset early and its new allowance is unused", lines[-1])

    def test_recurring_reset_sends_at_the_reset_unless_its_allowance_is_in_use(self):
        for reading, expected in (
            (self.reset_limits(100), "The reset time has arrived (5-hour: 100% used"),
            (RuntimeError("no answer"), "The reset time has arrived (the limits could not be read: no answer)"),
            (self.reset_limits(0), "The limit has reset and its new allowance is unused"),
        ):
            with self.subTest(expected=expected):
                checks, sends, lines = self.run_recurring_reset(
                    [self.reset_limits(100), self.reset_limits(100), reading], minutes_to_reset=70)
                self.assertEqual(checks, [-40, -10, 0.05])
                self.assertEqual(sends, 1)
                self.assertEqual(lines[1], f"Not sending yet (5-hour: 100% used, resets {self.SHOWN_RESET}; weekly: "
                                           "40% used, resets not set); last check at the reset, 20.10.2026 kl. 13.10.")
                self.assertTrue(lines[-1].startswith(expected), lines[-1])

    def window_limits(self, used, resets, allowed=True):
        return {"ordinaryUsageAllowed": allowed, "rateLimits": {
            "primary": {"usedPercent": used, "windowDurationMins": 300,
                        "resetsAt": None if resets is None else int(resets.timestamp())},
            "secondary": {"usedPercent": 10, "windowDurationMins": 10080,
                          "resetsAt": int(datetime.fromisoformat("2026-10-25T02:55:00+02:00").timestamp())},
        }}

    def test_start_window_sends_only_when_no_five_hour_window_is_running(self):
        now = datetime.fromisoformat("2026-10-20T00:00:00+02:00")
        for used, resets, allowed, fragment in (
            (55, now + timedelta(hours=2), False,
             f"already running (55% used, resets {(now + timedelta(hours=2)).astimezone():%d.%m.%Y kl. %H.%M})"),
            (1, now + timedelta(hours=4, minutes=59), True, "already running"),
            (0, now + timedelta(hours=3), True, "already running"),
            (0, now + timedelta(hours=5), True, None),
            (0, now + timedelta(hours=4, minutes=59), True, None),
            (80, now - timedelta(minutes=5), True, None),
            (0, None, True, None),
            (0, now + timedelta(hours=5), False, "a usage limit is used up"),
        ):
            with self.subTest(used=used, resets=resets, allowed=allowed):
                with patch.object(codex_resume, "app_server_request",
                                  return_value=self.window_limits(used, resets, allowed)):
                    reason = codex_resume.start_window_skip_reason(now)
                if fragment is None:
                    self.assertIsNone(reason)
                else:
                    self.assertIn(fragment, reason)

    def test_start_window_skips_or_sends_scheduled_and_now_runs(self):
        for reason, sends in (("a 5-hour window is already running", False), (None, True)):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                plist_path = Path(temporary) / "job.plist"
                plist_path.write_text("scheduled")
                output = io.StringIO()
                with (
                    patch.object(codex_resume, "PLIST_PATH", plist_path),
                    patch.object(codex_resume, "wait_until_scheduled_time"),
                    patch.object(codex_resume, "start_window_skip_reason", return_value=reason),
                    patch.object(codex_resume, "latest_chat", return_value=self.LOCAL_CHAT),
                    patch.object(codex_resume, "resume_through_queue") as queue,
                    patch.object(codex_resume, "launchctl") as launchctl,
                    redirect_stdout(output),
                ):
                    self.assertEqual(codex_resume.main(
                        ["--start-window", "--scheduled", datetime.now().astimezone().isoformat()]), 0)
                    self.assertEqual(codex_resume.main(["--now", "--start-window"]), 0)
                self.assertEqual(queue.call_count, 2 if sends else 0)
                self.assertFalse(plist_path.exists())
                launchctl.assert_called_once()
                expected = ("No 5-hour window is running; sending to start one." if sends
                            else "Not sending: a 5-hour window is already running.")
                lines = output.getvalue().splitlines()
                self.assertRegex(lines[0], r"^\[\d\d\.\d\d\.\d{4} kl\. \d\d\.\d\d\.\d\d\] " + re.escape(expected) + "$")
                if sends:
                    self.assertEqual(lines[-2:], [
                        expected, "Resuming in the latest chat, 'Find current project status'.",
                    ])
                else:
                    self.assertEqual(lines[-1], expected)
                self.assertEqual(len(lines), 3 if sends else 2)

    def test_start_window_is_stored_shown_and_needs_time_or_now(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "next_occurrence",
                             return_value=datetime.fromisoformat("2026-10-20T00:00:00+02:00")),
                patch.object(codex_resume, "launchctl"),
                patch.object(codex_resume, "start_window_skip_reason") as check,
                redirect_stdout(output),
            ):
                codex_resume.main(["--time", "00:00", "--start-window"])
                codex_resume.main(["--status"])
            check.assert_not_called()
            with plist_path.open("rb") as agent_file:
                arguments = plistlib.load(agent_file)["ProgramArguments"]
        self.assertEqual(arguments[-3:], ["--start-window", "--scheduled", "2026-10-20T00:00:00+02:00"])
        self.assertEqual(output.getvalue().splitlines(), [
            "Codex resume scheduled for 20.10.2026 kl. 00.00 in the latest chat on this Mac, "
            "only if no 5-hour window is running.",
            "",
            "─ Recurring check ──────────────────────────────────────────",
            "  Not set up. Start it with --recurring <minutes>.",
            "",
            "─ Scheduled resume ─────────────────────────────────────────",
            "  At          20.10.2026 kl. 00.00",
            "  Chat        latest chat on this Mac",
            "  Message     continue (not sent if Codex is already replying)",
            "  Only if     no 5-hour window is running",
        ])
        for action in (["--reset"], ["--status"], ["--list"]):
            with self.subTest(action=action), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_resume.main([*action, "--start-window"])
                self.assertEqual(error.exception.code, 2)



if __name__ == "__main__":
    unittest.main()
