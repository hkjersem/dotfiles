"""Offline regression tests for codex-resume scheduling."""

from datetime import datetime, timedelta
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/codex-resume.py"
SPEC = importlib.util.spec_from_file_location("codex_resume", SCRIPT)
codex_resume = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_resume)

THREAD_COLUMNS = (
    "id TEXT, name TEXT, title TEXT, cwd TEXT, source TEXT, archived INTEGER, "
    "recency_at_ms INTEGER, updated_at_ms INTEGER, updated_at INTEGER"
)


def write_threads(path, rows):
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE threads ({THREAD_COLUMNS})")
    connection.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def ms(text):
    return int(datetime.fromisoformat(text).timestamp() * 1000)


FAKE_APP_SERVER = """#!/usr/bin/env python3
import json, sys
mode = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "app-server" else "{mode}"
for line in sys.stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        print(json.dumps({{"id": 1, "result": {{}}}}), flush=True)
    elif request.get("id") == 2 and mode == "exit":
        sys.exit(0)
    elif request.get("id") == 2 and mode != "silent":
        if mode == "error":
            reply = {{"id": 2, "error": {{"message": "not signed in"}}}}
        else:
            reply = {{"id": 2, "result": {{"method": request["method"]}}}}
        print("not json", flush=True)
        print(json.dumps(reply), flush=True)
"""


class CodexResumeTests(unittest.TestCase):
    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("swift"),
        "Native Accessibility self-tests require macOS and Swift.",
    )
    def test_native_composer_selection_and_submission_guards(self):
        result = subprocess.run(
            ["swift", str(codex_resume.HELPER_PATH), "--self-test"],
            text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = re.search(r"(\d+)/(\d+) self-tests passed", result.stdout)
        self.assertIsNotNone(summary, result.stdout)
        passed, total = (int(count) for count in summary.groups())
        self.assertGreater(total, 0)
        self.assertEqual(passed, total, result.stdout)

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
                with self.subTest(value=value):
                    self.assertEqual(
                        codex_resume.next_occurrence(value, now),
                        datetime.fromisoformat(expected),
                    )

    def test_next_occurrence_uses_today_if_time_is_ahead(self):
        now = datetime.fromisoformat("2026-10-16T13:00:00+02:00")
        self.assertEqual(
            codex_resume.next_occurrence("16:30", now),
            datetime.fromisoformat("2026-10-16T16:30:00+02:00"),
        )

    def test_next_occurrence_moves_elapsed_time_to_tomorrow(self):
        now = datetime.fromisoformat("2026-10-16T16:30:00+02:00")
        self.assertEqual(
            codex_resume.next_occurrence("16:30", now),
            datetime.fromisoformat("2026-10-17T16:30:00+02:00"),
        )

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

    def test_automation_invokes_native_helper_and_preserves_output(self):
        output = io.StringIO()
        result = subprocess.CompletedProcess([], 0, "Composer text:\n test \n", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
            redirect_stdout(output),
        ):
            codex_resume.run_automation("--inspect")
        run.assert_called_once_with(
            ["/usr/bin/swift", str(codex_resume.HELPER_PATH), "--inspect"],
            text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(output.getvalue(), result.stdout)

    def test_automation_failure_is_not_reported_as_success(self):
        result = subprocess.CompletedProcess([], 1, "", "Composer is ambiguous.")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result),
        ):
            with self.assertRaisesRegex(RuntimeError, "Composer is ambiguous"):
                codex_resume.run_automation("--submit")

    def test_unconfirmed_press_warns_against_duplicate_submission(self):
        result = subprocess.CompletedProcess([], 3, "", "Composer did not clear.")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result),
        ):
            with self.assertRaisesRegex(RuntimeError, "message may have been sent"):
                codex_resume.run_automation("--submit")

    def test_native_retry_status_is_only_retryable_in_scheduled_mode(self):
        result = subprocess.CompletedProcess([], 4, "", "Mac is active; no input sent.")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result),
        ):
            for action in codex_resume.SCHEDULED_ACTIONS:
                with self.assertRaises(codex_resume.RetryableAutomationError):
                    codex_resume.run_automation(action)
            with self.assertRaises(RuntimeError) as error:
                codex_resume.run_automation("--submit")
            self.assertNotIsInstance(error.exception, codex_resume.RetryableAutomationError)

    def test_schedule_retries_a_safe_failure_then_stops_on_success(self):
        with (
            patch.object(codex_resume, "submit", side_effect=[
                codex_resume.RetryableAutomationError("No focused window; no input sent."),
                None,
            ]) as submit,
            patch.object(codex_resume.time, "monotonic", side_effect=[0, 10, 60]),
            patch.object(codex_resume.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            codex_resume.submit_scheduled()
        self.assertEqual(submit.call_args_list, [
            call("--scheduled-initial-submit", None, None), call("--scheduled-submit", None, None),
        ])
        sleep.assert_called_once_with(50)

    def test_native_exit_codes_drive_retry_without_repeating_uncertain_submission(self):
        for outcomes, expected_calls in (
            ([
                subprocess.CompletedProcess([], 4, "", "No focused window; no input sent."),
                subprocess.CompletedProcess([], 0, "Submission confirmed.\n", ""),
            ], 2),
            ([subprocess.CompletedProcess([], 3, "", "Return sent; not confirmed.")], 1),
        ):
            with self.subTest(expected_calls=expected_calls):
                with (
                    patch.object(codex_resume.shutil, "which",
                                 return_value="/usr/bin/swift"),
                    patch.object(codex_resume.subprocess, "run",
                                 side_effect=outcomes) as run,
                    patch.object(codex_resume.time, "monotonic", return_value=0),
                    patch.object(codex_resume.time, "sleep") as sleep,
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
                ):
                    if expected_calls == 1:
                        with self.assertRaisesRegex(RuntimeError, "may have been sent"):
                            codex_resume.submit_scheduled()
                    else:
                        codex_resume.submit_scheduled()
                self.assertEqual(run.call_count, expected_calls)
                self.assertEqual(
                    [entry.args[0][-1] for entry in run.call_args_list],
                    ["--scheduled-initial-submit"]
                    + ["--scheduled-submit"] * (expected_calls - 1),
                )
                self.assertEqual(sleep.call_count, expected_calls - 1)

    def test_schedule_exhausts_ten_retries_without_infinite_wait(self):
        with (
            patch.object(codex_resume, "submit", side_effect=
                         codex_resume.RetryableAutomationError("Mac is active.")) as submit,
            patch.object(codex_resume.time, "monotonic", return_value=0),
            patch.object(codex_resume.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "retry window exhausted"):
                codex_resume.submit_scheduled()
        self.assertEqual(submit.call_count, 11)
        self.assertEqual(
            submit.call_args_list,
            [call("--scheduled-initial-submit", None, None)] + [call("--scheduled-submit", None, None)] * 9
            + [call("--scheduled-final-submit", None, None)],
        )
        self.assertEqual(sleep.call_args_list, [call(60)] * 10)

    def test_schedule_never_retries_uncertain_or_permanent_failure(self):
        for error in (
            RuntimeError("message may have been sent"),
            RuntimeError("Composer is ambiguous"),
            RuntimeError("Typed continue, but Send is still disabled"),
        ):
            with self.subTest(error=str(error)):
                with (
                    patch.object(codex_resume, "submit", side_effect=error) as submit,
                    patch.object(codex_resume.time, "sleep") as sleep,
                    redirect_stdout(io.StringIO()),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        codex_resume.submit_scheduled()
                self.assertIs(raised.exception, error)
                submit.assert_called_once_with("--scheduled-initial-submit", None, None)
                sleep.assert_not_called()

    def test_schedule_stops_retrying_if_a_later_attempt_may_have_sent_input(self):
        with (
            patch.object(codex_resume, "submit", side_effect=[
                codex_resume.RetryableAutomationError("No focused window"),
                RuntimeError("Submission state is unknown"),
            ]) as submit,
            patch.object(codex_resume.time, "monotonic", return_value=0),
            patch.object(codex_resume.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "Submission state is unknown"):
                codex_resume.submit_scheduled()
        self.assertEqual(submit.call_count, 2)
        sleep.assert_called_once_with(60)

    def test_automation_timeout_reports_unknown_submission_state(self):
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run",
                         side_effect=subprocess.TimeoutExpired("swift", 60)),
        ):
            with self.assertRaisesRegex(RuntimeError, "submission state is unknown"):
                codex_resume.run_automation("--submit")

    def test_missing_swift_reports_requirement(self):
        with patch.object(codex_resume.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Xcode Command Line Tools"):
                codex_resume.run_automation("--check")

    def test_help_lists_supported_flags(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_code:
                codex_resume.main(["--help"])
        self.assertEqual(exit_code.exception.code, 0)
        for flag in ("--time", "--status", "--cancel", "--now", "--inspect", "--check"):
            self.assertIn(flag, output.getvalue())
        self.assertNotIn("--scheduled", output.getvalue())
        self.assertIn("Submission activates the app", output.getvalue())

    def test_actions_are_flags_and_must_be_selected_once(self):
        with self.assertRaises(SystemExit) as no_action:
            codex_resume.main([])
        self.assertEqual(no_action.exception.code, 2)
        with self.assertRaises(SystemExit) as multiple_actions:
            codex_resume.main(["--status", "--cancel"])
        self.assertEqual(multiple_actions.exception.code, 2)

    def test_inspect_check_and_now_dispatch_separate_actions(self):
        for flag, expected in (
            ("--inspect", call("--inspect", chat=None)), ("--check", call("--check", chat=None)),
            ("--now", call("--submit", None, None)),
        ):
            with self.subTest(flag=flag):
                with patch.object(codex_resume, "run_automation") as run:
                    self.assertEqual(codex_resume.main([flag]), 0)
                self.assertEqual(run.call_args_list, [expected])

    def test_schedule_starts_caffeinate_immediately_until_submission_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "com.hkjersem.codex-resume.plist"
            log_path = Path(temporary) / "codex-resume.log"
            now = datetime.fromisoformat("2026-10-16T13:00:00+02:00")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", log_path),
                patch.object(codex_resume, "launchctl") as launchctl,
                patch("builtins.print"),
            ):
                codex_resume.schedule(codex_resume.next_occurrence("16:30", now))

            with plist_path.open("rb") as agent_file:
                plist = plistlib.load(agent_file)
            self.assertEqual(plist["Label"], codex_resume.LABEL)
            self.assertTrue(plist["RunAtLoad"])
            self.assertNotIn("StartCalendarInterval", plist)
            self.assertEqual(plist["ProgramArguments"][:2], ["/usr/bin/caffeinate", "-di"])
            self.assertEqual(plist["ProgramArguments"][2:4], [
                sys.executable, str(codex_resume.SCRIPT_PATH),
            ])
            self.assertEqual(plist["ProgramArguments"][-2:], [
                "--scheduled", "2026-10-16T16:30:00+02:00",
            ])
            launchctl.assert_called_once_with(
                "bootstrap", codex_resume.agent_domain(), str(plist_path)
            )

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

    def test_scheduled_run_submits_and_unloads_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time") as wait,
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl") as launchctl,
            ):
                self.assertEqual(
                    codex_resume.main(["--scheduled", "2026-10-16T16:30:00+02:00"]),
                    0,
                )
            wait.assert_called_once_with("2026-10-16T16:30:00+02:00")
            submit.assert_called_once_with("--scheduled-initial-submit", None, None)
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
                patch.object(codex_resume, "submit", side_effect=RuntimeError("Send failed")),
                patch.object(codex_resume, "launchctl", side_effect=bootout),
                redirect_stderr(stderr),
            ):
                with self.assertRaises(SystemExit):
                    codex_resume.main(["--scheduled", "2026-10-16T16:30:00+02:00"])
            self.assertFalse(plist_path.exists())

    def test_exhausted_retries_unload_job_and_remove_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_text("scheduled")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "submit", side_effect=
                             codex_resume.RetryableAutomationError("Mac is active.")) as submit,
                patch.object(codex_resume.time, "monotonic", return_value=0),
                patch.object(codex_resume.time, "sleep"),
                patch.object(codex_resume, "launchctl") as launchctl,
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    codex_resume.main(["--scheduled", "2026-10-16T16:30:00+02:00"]), 1
                )
            self.assertEqual(submit.call_count, 11)
            self.assertFalse(plist_path.exists())
            launchctl.assert_called_once_with(
                "bootout", f"{codex_resume.agent_domain()}/{codex_resume.LABEL}"
            )

    def test_message_is_validated_and_passed_to_the_helper(self):
        result = subprocess.CompletedProcess([], 0, "Sent.\n", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(codex_resume.main(["--now", "--message", "  Resume if cut off  "]), 0)
        run.assert_called_once_with(
            ["/usr/bin/swift", str(codex_resume.HELPER_PATH), "--submit",
             "--message", "Resume if cut off"],
            text=True, capture_output=True, timeout=60,
        )

    def test_invalid_messages_are_rejected_before_any_automation(self):
        for value, fragment in (
            ("   ", "must not be empty"), ("one\ntwo", "single line"),
            ("tab\there", "single line"), ("/compact", "must not start with '/'"),
        ):
            with self.subTest(value=value):
                with patch.object(codex_resume, "run_automation") as run:
                    with self.assertRaisesRegex(ValueError, fragment):
                        codex_resume.main(["--now", "--message", value])
                run.assert_not_called()

    def test_message_requires_time_or_now(self):
        for action in (["--status"], ["--cancel"], ["--check"], ["--inspect"]):
            with self.subTest(action=action):
                with (
                    patch.object(codex_resume, "run_automation") as run,
                    redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as error:
                        codex_resume.main([*action, "--message", "resume"])
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_scheduled_message_is_stored_shown_and_used_for_every_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "com.hkjersem.codex-resume.plist"
            now = datetime.fromisoformat("2026-10-16T13:00:00+02:00")
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "next_occurrence",
                              return_value=codex_resume.next_occurrence("16:30", now)),
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
                "Codex resume scheduled for 2026-10-16 16:30 with message: Resume now",
            ] * 2)

            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "submit", side_effect=[
                    codex_resume.RetryableAutomationError("The composer holds a draft"), None,
                ]) as submit,
                patch.object(codex_resume.time, "monotonic", return_value=0),
                patch.object(codex_resume.time, "sleep"),
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(submit.call_args_list, [
                call("--scheduled-initial-submit", "Resume now", None),
                call("--scheduled-submit", "Resume now", None),
            ])

    def chat_rows(self):
        return [
            ("01a1-mine", "Find current project status", "where are we", "/work/myntr", "vscode", 0,
             ms("2026-09-04T13:56:00+02:00"), 0, 0),
            ("01a2-new", None, "  Fresh\n  prompt  ", "/work/dash", "cli", 0,
             0, ms("2026-10-18T09:00:00+02:00"), 0),
            ("01a3-guard", "Guardian review", "Guardian review", "/work", '{"subagent":{"other":"guardian"}}', 0,
             ms("2026-10-19T11:29:00+02:00"), 0, 0),
            ("01a4-old", "Find current project status", "x", "/work/old", "vscode", 0,
             ms("2026-08-01T10:00:00+02:00"), 0, 0),
            ("01a5-arch", "Archived plan", "x", "/work", "vscode", 1,
             ms("2026-10-19T10:00:00+02:00"), 0, 0),
            ("01a6-status", "Status board", "x", "/work", "vscode", 0,
             ms("2026-07-01T10:00:00+02:00"), 0, 0),
        ]

    def with_chats(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        write_threads(home / "state_5.sqlite", self.chat_rows())
        write_threads(home / "state_4.sqlite", [])
        patcher = patch.object(codex_resume, "CODEX_HOME", home)
        patcher.start()
        self.addCleanup(patcher.stop)
        return home

    def test_chat_list_shows_only_own_unarchived_chats_newest_first(self):
        self.with_chats()
        chats = codex_resume.load_chats()
        self.assertEqual([chat["id"] for chat in chats], ["01a2-new", "01a1-mine", "01a4-old", "01a6-status"])
        self.assertEqual(chats[0]["name"], "Fresh prompt")
        output = io.StringIO()
        with redirect_stdout(output):
            codex_resume.list_chats("status")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[0], r"^2026-09-04 \d\d:56  01a1-mine  Find current project status\s+myntr$")
        self.assertNotIn("Guardian", output.getvalue())

    def test_missing_state_database_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(codex_resume, "CODEX_HOME", Path(temporary)):
            with self.assertRaisesRegex(RuntimeError, "No Codex state database"):
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
        self.assertRegex(message, r"2026-09-04 \d\d:56  01a1-mine")
        self.assertRegex(message, r"2026-08-01 \d\d:00  01a4-old")
        for query in ("guardian", "archived", "nothing like this"):
            with self.subTest(query=query), self.assertRaisesRegex(RuntimeError, "No chat matches"):
                codex_resume.resolve_chat(query)

    def test_chat_is_passed_to_the_helper_and_stored_for_scheduled_runs(self):
        self.with_chats()
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
        ):
            self.assertEqual(codex_resume.main(["--now", "--chat", "01a1"]), 0)
        self.assertEqual(run.call_args.args[0][2:], [
            "--submit", "--chat", "01a1-mine", "--chat-title", "Find current project status",
        ])
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
            self.assertEqual(output.getvalue().count("in chat 'Status board'"), 2)
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(submit.call_args.args[2]["name"], "Status board")

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
            codex_resume.app_server_request("account/rateLimits/read"),
            {"method": "account/rateLimits/read"},
        )

    def test_app_server_errors_and_silence_are_reported(self):
        self.fake_cli("error")
        with self.assertRaisesRegex(RuntimeError, "rejected account/rateLimits/read: not signed in"):
            codex_resume.app_server_request("account/rateLimits/read")
        self.fake_cli("exit")
        with self.assertRaisesRegex(RuntimeError, "exited without answering"):
            codex_resume.app_server_request("account/rateLimits/read")
        self.fake_cli("silent")
        with patch.object(codex_resume, "APP_SERVER_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(RuntimeError, "did not answer"):
                codex_resume.app_server_request("account/rateLimits/read")

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
        self.assertIn("5-hour: 98% used, resets 2026-10-19", str(error.exception))
        self.assertIn("weekly: 76% used, resets 2026-10-25", str(error.exception))

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

    def test_compile_error_on_old_swift_explains_the_update(self):
        compile_error = subprocess.CompletedProcess(
            [], 1, "", "codex-resume.swift:280:23: error: variable binding in a condition",
        )
        for version_output, fragment in (
            ("Apple Swift version 5.4 (swiftlang-1205.0.26.9)", "Swift 5.4 cannot compile"),
            ("Apple Swift version 6.4 (swiftlang-6.4.0.34.1)", "Codex automation failed"),
        ):
            with self.subTest(version=version_output):
                version = subprocess.CompletedProcess([], 0, version_output, "")
                with (
                    patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
                    patch.object(codex_resume.subprocess, "run",
                                 side_effect=[compile_error, version]),
                ):
                    with self.assertRaisesRegex(RuntimeError, fragment):
                        codex_resume.run_automation("--submit")


if __name__ == "__main__":
    unittest.main()
