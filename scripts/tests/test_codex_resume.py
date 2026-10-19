"""Offline regression tests for codex-resume scheduling."""

from datetime import datetime, timedelta
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
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
            codex_resume.run_automation("--submit")
        run.assert_called_once_with(
            ["/usr/bin/swift", str(codex_resume.HELPER_PATH), "--submit"],
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

    def test_chat_list_shows_this_and_remote_macs_chats_newest_first(self):
        self.with_chats()
        chats = codex_resume.load_chats()
        self.assertEqual([chat["id"] for chat in chats],
                         ["01a2-new", "01a7-mini", "01a1-mine", "01a4-old", "01a6-status"])
        self.assertEqual(chats[0]["name"], "Fresh prompt")
        self.assertEqual([chat["host"] for chat in chats[:2]], [None, REMOTE_HOST])
        output = io.StringIO()
        with redirect_stdout(output):
            codex_resume.list_chats("status")
            codex_resume.list_chats("pwa")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[0], r"^2026-09-04 \d\d:56  01a1-mine  Find current project status\s+myntr$")
        self.assertRegex(lines[2], r"^2026-10-10 \d\d:00  01a7-mini  Explore PWA roadmap  local-relay \(remote\)$")
        self.assertNotIn("Guardian", output.getvalue())

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
        self.assertRegex(message, r"2026-09-04 \d\d:56  01a1-mine")
        self.assertRegex(message, r"2026-08-01 \d\d:00  01a4-old")
        for query in ("guardian", "removed", "plain chatgpt", "nothing like this"):
            with self.subTest(query=query), self.assertRaisesRegex(RuntimeError, "No chat matches"):
                codex_resume.resolve_chat(query)

    def test_chat_sharing_its_window_title_with_another_is_refused(self):
        def chat(chat_id, name):
            return {"id": chat_id, "name": name, "cwd": None, "updated": datetime.now().astimezone(), "host": None}

        for other in ("Plan the move", "Plan  the move", "Plan the…", "Plan the move and more"):
            with self.subTest(other=other):
                with patch.object(codex_resume, "load_chats", return_value=[chat("a", "Plan the move"), chat("b", other)]):
                    clash = other != "Plan the move and more"
                    if clash:
                        with self.assertRaisesRegex(RuntimeError, "same name as 'Plan the move'.*\n.*  a  .*\n.*  b  "):
                            codex_resume.resolve_chat("a")
                    else:
                        self.assertEqual(codex_resume.resolve_chat("a")["id"], "a")

    def test_chat_is_passed_to_the_helper_and_stored_for_scheduled_runs(self):
        self.with_chats()
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--chat", "01a1"]), 0)
            now_arguments = run.call_args.args[0][2:]
            self.assertEqual(codex_resume.main(["--now", "--chat", "explore pwa"]), 0)
        self.assertEqual(now_arguments, [
            "--submit", "--chat", "01a1-mine", "--chat-title", "Find current project status",
        ])
        self.assertEqual(output.getvalue().splitlines(), [
            "Resuming in chat 'Find current project status'.",
            "Resuming in chat 'Explore PWA roadmap' (remote).",
        ])
        report = subprocess.CompletedProcess([], 0, '{"windowTitle": "Explore PWA roadmap"}', "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=report) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(codex_resume.main(["--check", "--chat", "explore pwa"]), 0)
        self.assertEqual(run.call_args.args[0][2:], [
            "--check", "--chat", "01a7-mini", "--chat-title", "Explore PWA roadmap", "--chat-host", REMOTE_HOST,
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
                redirect_stdout(io.StringIO()) as log,
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(submit.call_args.args[2]["name"], "Status board")
            self.assertIn("attempt 1/11 in chat 'Status board'.", log.getvalue())

    def test_check_and_inspect_identify_the_open_chat_and_its_project(self):
        self.with_chats()
        for title, chat in (
            ("Explore PWA roadmap", {"id": "01a7-mini", "name": "Explore PWA roadmap", "remote": True,
                                     "project": "local-relay", "folder": "/mini/local-relay"}),
            ("Find current project…", None),
            ("Codex", None),
        ):
            for action in ("--check", "--inspect"):
                with self.subTest(title=title, action=action):
                    result = subprocess.CompletedProcess([], 0, json.dumps({"windowTitle": title, "empty": True}), "")
                    with (
                        patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
                        patch.object(codex_resume.subprocess, "run", return_value=result),
                        redirect_stdout(io.StringIO()) as output,
                    ):
                        codex_resume.run_automation(action)
                    self.assertEqual(json.loads(output.getvalue()),
                                     {"windowTitle": title, "empty": True, "chat": chat})

    def test_project_matches_folder_names_and_picks_its_latest_chat(self):
        self.with_chats()
        for query, chat_id in (("myntr", "01a1-mine"), ("LOCAL rel", "01a7-mini"), ("/work", "01a6-status")):
            with self.subTest(query=query):
                self.assertEqual(codex_resume.resolve_project(query)[1]["id"], chat_id)
        with self.assertRaisesRegex(RuntimeError, "projects match 'o'") as error:
            codex_resume.resolve_project("o")
        self.assertIn("/mini/local-relay  'local-relay' (remote)", str(error.exception))
        with self.assertRaisesRegex(RuntimeError, "No project matches 'remote'"):
            codex_resume.resolve_project("remote")

    def test_scheduled_project_opens_the_chat_that_is_latest_when_it_runs(self):
        home = self.with_chats()
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--project", "local-relay"]), 0)
        self.assertEqual(run.call_args.args[0][3:5], ["--chat", "01a7-mini"])
        self.assertEqual(output.getvalue(),
                         "Resuming in chat 'Explore PWA roadmap' (remote) in project 'local-relay' (remote).\n")
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
            self.assertEqual(output.getvalue().count("in the latest chat in project 'myntr'"), 2)
            self.assertNotIn("in chat", output.getvalue())
            (home / codex_resume.CHAT_CATALOG).unlink()
            write_catalog(home / codex_resume.CHAT_CATALOG, self.chat_rows() + [
                ("local", "01a8-later", "Later myntr work", "/work/myntr", "user", 0,
                 0, seconds("2026-10-19T12:00:00+02:00")),
            ])
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()) as log,
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(submit.call_args.args[2]["id"], "01a8-later")
            self.assertIn("in chat 'Later myntr work'.", log.getvalue())

    def test_scheduled_project_without_chats_fails_and_unloads(self):
        self.with_chats()
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            plist_path.write_bytes(b"")
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl") as launchctl,
                redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(codex_resume.main(
                    ["--project", "/work/deleted", "--scheduled", "2026-10-19T16:30:00+02:00"]), 1)
            submit.assert_not_called()
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
            (55, now + timedelta(hours=2), False, "already running (55% used, resets 2026-10-20 02:00)"),
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
                    patch.object(codex_resume, "submit") as submit,
                    patch.object(codex_resume, "launchctl") as launchctl,
                    redirect_stdout(output),
                ):
                    self.assertEqual(codex_resume.main(
                        ["--start-window", "--scheduled", "2026-10-20T00:00:00+02:00"]), 0)
                    self.assertEqual(codex_resume.main(["--now", "--start-window"]), 0)
                self.assertEqual(submit.call_count, 2 if sends else 0)
                self.assertFalse(plist_path.exists())
                launchctl.assert_called_once()
                expected = ("No 5-hour window is running; sending to start one." if sends
                            else "Not sending: a 5-hour window is already running.")
                lines = output.getvalue().splitlines()
                self.assertRegex(lines[0], r"^\[\d\d\.\d\d\.\d{4} kl\. \d\d\.\d\d\.\d\d\] " + re.escape(expected) + "$")
                self.assertEqual(lines[-1], expected)
                self.assertEqual(len(lines), 3 if sends else 2)

    def test_start_window_is_stored_shown_and_needs_time_or_now(self):
        with tempfile.TemporaryDirectory() as temporary:
            plist_path = Path(temporary) / "job.plist"
            now = datetime.fromisoformat("2026-10-19T20:00:00+02:00")
            output = io.StringIO()
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "LOG_PATH", Path(temporary) / "log"),
                patch.object(codex_resume, "next_occurrence",
                             return_value=codex_resume.next_occurrence("00:00", now)),
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
            "Codex resume scheduled for 2026-10-20 00:00, only if no 5-hour window is running.",
        ] * 2)
        for action in (["--reset"], ["--status"], ["--check"]):
            with self.subTest(action=action), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_resume.main([*action, "--start-window"])
                self.assertEqual(error.exception.code, 2)

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
