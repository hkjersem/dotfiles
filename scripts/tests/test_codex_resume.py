"""Offline regression tests for codex-resume scheduling."""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager, redirect_stderr, redirect_stdout
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


class CodexResumeTests(unittest.TestCase):
    def setUp(self):
        # Never reach the real Codex CLI: a local chat would otherwise queue a real message.
        guard = patch.object(codex_resume, "app_server_request",
                             side_effect=AssertionError("unexpected Codex app server request"))
        guard.start()
        self.addCleanup(guard.stop)
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

    def test_scheduled_automation_wakes_the_display_while_the_helper_runs(self):
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result) as run,
            redirect_stdout(io.StringIO()),
        ):
            for action in codex_resume.SCHEDULED_ACTIONS:
                codex_resume.run_automation(action)
        self.assertEqual([command.args[0] for command in run.call_args_list], [
            ["/usr/bin/caffeinate", "-du", "/usr/bin/swift", str(codex_resume.HELPER_PATH), action]
            for action in codex_resume.SCHEDULED_ACTIONS
        ])

    def test_scheduled_helper_output_is_timestamped_in_the_log(self):
        result = subprocess.CompletedProcess([], 0, "Pressed Return.\nThe reply started.\n", "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=result),
            patch.object(codex_resume, "log_stamp", return_value="[t] "),
            redirect_stdout(io.StringIO()) as output,
        ):
            codex_resume.run_automation("--scheduled-submit")
        self.assertEqual(output.getvalue(), "[t] Pressed Return.\n[t] The reply started.\n")

    def test_a_late_scheduled_run_says_how_late_it_is(self):
        planned = datetime.now().astimezone() - timedelta(hours=2, minutes=36, seconds=10)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(codex_resume, "PLIST_PATH", Path(temporary) / "job.plist"),
                patch.object(codex_resume, "log_stamp", return_value=""),
                patch.object(codex_resume, "submit_scheduled") as submit,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(codex_resume.main(["--scheduled", planned.isoformat()]), 0)
        submit.assert_called_once_with(None, None)
        self.assertEqual(output.getvalue(),
                         "Running now (2 h 36 min late; the Mac was probably asleep or off).\n")
        self.assertEqual(codex_resume.late_note(planned, planned + timedelta(seconds=119)), "")
        self.assertEqual(codex_resume.late_note(planned, planned + timedelta(minutes=5)),
                         "5 min late; the Mac was probably asleep or off")

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
            patch.object(codex_resume, "log_stamp", return_value="[t] "),
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors,
        ):
            with self.assertRaisesRegex(RuntimeError, "Gave up after 11 attempts; nothing was sent"):
                codex_resume.submit_scheduled()
        self.assertEqual(errors.getvalue(), "[t] Mac is active.\n" * 11)
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
            with self.assertRaisesRegex(RuntimeError, "whether the message was sent is unknown"):
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
                # Typing into the open chat or a remote one needs the screen unlocked, so the display stays on.
                for chat, flags in ((self.LOCAL_CHAT, "-i"), ({**self.LOCAL_CHAT, "host": "mini"}, "-di")):
                    codex_resume.schedule(at_half_past_four, chat=chat)
                    with plist_path.open("rb") as agent_file:
                        self.assertEqual(plistlib.load(agent_file)["ProgramArguments"][1], flags)

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
                "Codex resume scheduled for 16.10.2026 kl. 16.30 via the app's Accessibility controls "
                "with message: Resume now",
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
        self.assertRegex(lines[0], rf"^{shown('2026-09-04T13:56:00+02:00')}  01a1-mine  Find current project status\s+myntr$")
        self.assertEqual(lines[2], f"{shown('2026-10-10T08:00:00+02:00')}  01a7-mini  Explore PWA roadmap  local-relay (remote)")
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
        self.assertIn(f"{shown('2026-09-04T13:56:00+02:00')}  01a1-mine", message)
        self.assertIn(f"{shown('2026-08-01T10:00:00+02:00')}  01a4-old", message)
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
            patch.object(codex_resume, "resume_through_queue", return_value=False) as queue,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(codex_resume.main(["--now", "--chat", "01a1"]), 0)
            now_arguments = run.call_args.args[0][2:]
            self.assertEqual(codex_resume.main(["--now", "--chat", "explore pwa"]), 0)
        self.assertEqual([entry.args[0]["id"] for entry in queue.call_args_list], ["01a1-mine"])
        self.assertEqual(now_arguments[:6], [
            "--submit", "--chat", "01a1-mine", "--chat-title", "Find current project status", "--other-titles",
        ])
        others = json.loads(now_arguments[6])
        self.assertIn("Explore PWA roadmap", others)
        self.assertNotIn("Find current project status", others)
        self.assertEqual(output.getvalue().splitlines(), [
            "Resuming in chat 'Find current project status' via the Codex queue.",
            "Resuming in chat 'Explore PWA roadmap' (remote) via the app's Accessibility controls.",
        ])
        report = subprocess.CompletedProcess([], 0, '{"windowTitle": "Explore PWA roadmap"}', "")
        with (
            patch.object(codex_resume.shutil, "which", return_value="/usr/bin/swift"),
            patch.object(codex_resume.subprocess, "run", return_value=report) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(codex_resume.main(["--check", "--chat", "explore pwa"]), 0)
        self.assertEqual(run.call_args.args[0][2:-1], [
            "--check", "--chat", "01a7-mini", "--chat-title", "Explore PWA roadmap", "--chat-host", REMOTE_HOST,
            "--other-titles",
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
            self.assertEqual(output.getvalue().count("in chat 'Status board' via the Codex queue."), 2)
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "resume_through_queue", return_value=False),
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()) as log,
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(submit.call_args.args[2]["name"], "Status board")
            self.assertIn("in chat 'Status board' through the app's Accessibility controls, attempt 1 of 11.", log.getvalue())

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
                         "Resuming in chat 'Explore PWA roadmap' (remote) in project 'local-relay' (remote) "
                         "via the app's Accessibility controls.\n")
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
            self.assertEqual(output.getvalue().count("in the latest chat in project 'myntr' via the Codex queue."), 2)
            self.assertNotIn("in chat", output.getvalue())
            (home / codex_resume.CHAT_CATALOG).unlink()
            write_catalog(home / codex_resume.CHAT_CATALOG, self.chat_rows() + [
                ("local", "01a8-later", "Later myntr work", "/work/myntr", "user", 0,
                 0, seconds("2026-10-19T12:00:00+02:00")),
            ])
            with (
                patch.object(codex_resume, "PLIST_PATH", plist_path),
                patch.object(codex_resume, "wait_until_scheduled_time"),
                patch.object(codex_resume, "resume_through_queue", return_value=True) as queue,
                patch.object(codex_resume, "submit") as submit,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(codex_resume.main(arguments[4:]), 0)
            self.assertEqual(queue.call_args.args[0]["id"], "01a8-later")
            submit.assert_not_called()

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

    def fake_queue(self, listings, deleted=True, add_error=None, on_add=None, home=None):
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
            if method == "thread/queue/delete":
                return {"deleted": deleted}
            raise AssertionError(f"unexpected {method}")

        if home is None:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            home = Path(temporary.name)
        for patcher in (
            patch.object(codex_resume, "app_server_request", side_effect=request),
            patch.object(codex_resume, "CODEX_HOME", home),
            patch.object(codex_resume, "REPLY_RESULT_SECONDS", 0),
            patch.object(codex_resume.time, "sleep"),
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

    LOCAL_CHAT = {"id": "01a1-mine", "name": "Find current project status", "cwd": "/work/myntr", "host": None}

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

    def test_stuck_chats_are_local_recent_and_stopped_near_the_latest_limit_newest_first(self):
        now = datetime.fromisoformat("2026-10-27T10:00:00+02:00")
        closed = now - timedelta(days=6)
        chats = {
            "older": (closed, closed + timedelta(hours=2)),
            "latest": (closed + timedelta(hours=3), closed + timedelta(hours=3)),
            "edge": (closed - timedelta(hours=21), closed - timedelta(hours=21)),
            "earlier-limit": (closed - timedelta(hours=22), closed),
            "remote": (closed, closed + timedelta(hours=5)),
            "stale": (closed, closed + timedelta(hours=5)),
            "fine": None,
        }
        catalogue = [{"id": name, "name": name, "host": "mini" if name == "remote" else None,
                      "updated": now - timedelta(days=9 if name == "stale" else 5)} for name in chats]

        def stuck(chat, since):
            self.assertIsNone(since)
            self.assertIsNone(chat["host"])
            self.assertNotEqual(chat["id"], "stale")
            times = chats[chat["id"]]
            return times and {"run": chat["id"], "closed": times[0], "stopped": times[1]}

        with (patch.object(codex_resume, "load_chats", return_value=catalogue),
              patch.object(codex_resume, "stuck_at_limit", side_effect=stuck)):
            found = codex_resume.stuck_chats(now)
        self.assertEqual([chat["id"] for chat in found], ["latest", "older", "edge"])
        self.assertEqual(found[0]["name"], "latest")

    def recurring(self, chats, allowed=True, state=None, queued=True, now="2026-10-21T10:00:00+02:00",
                  stopped="2026-10-21T01:00:00+02:00"):
        """Run one recurring check with chats stuck since stopped; return the chats it resumed, what it printed,
        and the state it saved."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state/recurring.json"
        if state is not None:
            state_path.parent.mkdir()
            state_path.write_text(state if isinstance(state, str) else json.dumps(state))
        resumed = []

        def resume(chat, message, stamp, fallback):
            self.assertIsNone(message)
            self.assertFalse(fallback)
            # The resume is recorded before anything is queued, so a crash cannot send it twice.
            self.assertEqual(json.loads(state_path.read_text())["resumed"][chat["id"]], now)
            resumed.append(chat["id"])
            return queued

        def stuck(moment, since):
            # As the real one does, stops before a chat's last resume do not count.
            stop = datetime.fromisoformat(stopped)
            return [{**self.LOCAL_CHAT, "id": name, "name": name, "stopped": stop}
                    for name in chats if name not in since or since[name] < stop]

        with (patch.object(codex_resume, "RECURRING_STATE_PATH", state_path),
              patch.object(codex_resume, "stuck_chats", side_effect=stuck),
              patch.object(codex_resume, "usage_allowed", return_value=allowed) as usage,
              patch.object(codex_resume, "resume_through_queue", side_effect=resume),
              patch.object(codex_resume, "log_stamp", return_value="[stamp] "),
              redirect_stdout(io.StringIO()) as output):
            self.usage = usage
            codex_resume.recurring_check(datetime.fromisoformat(now))
        return resumed, output.getvalue(), json.loads(state_path.read_text())

    def test_recurring_check_resumes_up_to_three_stuck_chats_once_per_stop(self):
        resumed, output, state = self.recurring(["a", "b", "c", "d"])
        self.assertEqual(resumed, ["a", "b", "c"])
        self.assertEqual(output.splitlines()[0], f"[stamp] Chat 'a' stopped at a usage limit "
                                                 f"{shown('2026-10-21T01:00:00+02:00')}; resuming it.")
        self.assertEqual(state["checked"], "2026-10-21T10:00:00+02:00")
        self.assertEqual(sorted(state["resumed"]), ["a", "b", "c"])
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
              patch.object(codex_resume, "resume_through_queue",
                           side_effect=lambda chat, *_, **__: sent.append(chat["id"]) or True),
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
                      '{"resumed": {"a": "2026-10-21T09:00:00"}}'):
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

    def test_recurring_check_retries_a_chat_when_nothing_was_queued(self):
        resumed, _, state = self.recurring(["a"], queued=False)
        self.assertEqual((resumed, state["resumed"]), (["a"], {}))
        self.assertEqual(self.recurring(["a"], state=state)[0], ["a"])
        earlier = {"resumed": {"a": "2026-10-20T10:00:00+02:00"}}
        resumed, _, state = self.recurring(["a"], state=earlier, queued=False)
        self.assertEqual((resumed, state["resumed"]), (["a"], earlier["resumed"]))

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
                with redirect_stdout(recurring_status), patch.object(codex_resume, "PLIST_PATH",
                                                                     Path(temporary) / "none.plist"):
                    codex_resume.status()
                    state_path.write_text("{")
                    codex_resume.status()
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
            self.assertEqual(recurring_status.getvalue().splitlines(), [
                f"{lines[0]} Last checked {shown('2026-10-21T10:00:00+02:00')}.",
                "No Codex resume is scheduled.",
                f"{lines[0]} Checks stop until this is fixed: cannot read {state_path} "
                "(Expecting property name enclosed in double quotes: line 1 column 2 (char 1)); fix or delete it.",
                "No Codex resume is scheduled.",
            ])

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
            self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        add = calls[0]
        self.assertEqual(add[0], "thread/queue/add")
        self.assertEqual(add[1]["threadId"], "01a1-mine")
        self.assertEqual(add[1]["input"], [{"type": "text", "text": "continue", "text_elements": []}])
        self.assertTrue(add[1]["clientUserMessageId"])
        self.assertEqual([call[1].get("cursor") for call in calls[1:]], [None, None, "c2", None])
        self.assertNotIn("thread/queue/delete", [call[0] for call in calls])
        self.assertEqual(output.getvalue().splitlines(), [
            "Queued the message in chat 'Find current project status'; waiting for the app to take it.",
            "The app took the message from the queue.",
        ])

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
                    self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
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
            self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        self.assertEqual(output.getvalue().splitlines()[-1], "The reply ended with an error: Usage limit.")
        self.assertFalse(codex_resume.written_since({"timestamp": "unreadable"}, datetime.now().astimezone()))
        self.assertFalse(codex_resume.written_since({}, datetime.now().astimezone()))

    def test_queue_leaves_a_reply_in_progress_alone(self):
        calls = self.fake_queue([self.queued("q1")])
        self.write_session_log("task_started", "task_complete", "task_started")
        with redirect_stdout(io.StringIO()) as output:
            self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, None, lambda: "[t] "))
        self.assertEqual(calls, [])
        self.assertEqual(output.getvalue(),
                         "[t] Codex is already replying in chat 'Find current project status'; not sending 'continue'.\n")
        with redirect_stdout(io.StringIO()) as output:
            self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, "After this"))
        self.assertEqual([call[0] for call in calls], ["thread/queue/add"])
        self.assertIn("queued the message, and the app sends it when the reply ends", output.getvalue())

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

    def test_queue_message_left_untaken_is_removed_before_the_app_controls_are_used(self):
        calls = self.fake_queue([self.queued("q1")])
        with (
            patch.object(codex_resume, "QUEUE_PICKUP_SECONDS", 0),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertFalse(codex_resume.resume_through_queue(self.LOCAL_CHAT, "Resume now", lambda: "[t] "))
        self.assertEqual(calls[0][1]["input"][0]["text"], "Resume now")
        self.assertEqual(calls[-1], ("thread/queue/delete", {"threadId": "01a1-mine", "queuedSubmissionId": "q1"}))
        self.assertIn("[t] The app did not take the message within 0 minutes, so it was removed",
                      output.getvalue())

    def test_queue_message_taken_while_removing_it_counts_as_sent(self):
        self.fake_queue([self.queued("q1")], deleted=False)
        with (
            patch.object(codex_resume, "QUEUE_PICKUP_SECONDS", 0),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertTrue(codex_resume.resume_through_queue(self.LOCAL_CHAT, None))
        self.assertTrue(output.getvalue().endswith("The app took the message from the queue.\n"))

    def test_queue_rejection_falls_back_and_uncertainty_stops(self):
        calls = self.fake_queue([self.queued()], add_error=codex_resume.AppServerRejectedError("thread not found"))
        with redirect_stdout(io.StringIO()) as output:
            self.assertFalse(codex_resume.resume_through_queue(self.LOCAL_CHAT, "Resume now"))
        self.assertEqual([call[0] for call in calls], ["thread/queue/add"])
        self.assertIn("Could not queue the message (thread not found)", output.getvalue())
        timeout = RuntimeError("The Codex app server did not answer in time.")
        for kind, setup in (
            ("may have been queued", {"listings": [self.queued()], "add_error": timeout}),
            ("whether the app took it is unknown", {"listings": [timeout]}),
        ):
            for message in (None, "Resume now"):
                with self.subTest(kind=kind, message=message):
                    self.fake_queue(**setup)
                    with redirect_stdout(io.StringIO()), \
                            self.assertRaisesRegex(RuntimeError, f"{kind}.*Check the chat before retrying"):
                        codex_resume.resume_through_queue(self.LOCAL_CHAT, message)

    def test_scheduled_queue_uncertainty_stops_without_typing(self):
        timeout = RuntimeError("The Codex app server did not answer in time.")
        for message in (None, "Resume now"):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                self.fake_queue([timeout], home=self.with_chats())
                plist_path = Path(temporary) / "job.plist"
                plist_path.write_text("scheduled")
                with (
                    patch.object(codex_resume, "PLIST_PATH", plist_path),
                    patch.object(codex_resume, "wait_until_scheduled_time"),
                    patch.object(codex_resume, "submit") as submit,
                    patch.object(codex_resume, "launchctl") as launchctl,
                    redirect_stdout(io.StringIO()) as log,
                    redirect_stderr(io.StringIO()) as errors,
                ):
                    self.assertEqual(codex_resume.main(
                        [*(["--message", message] if message else []), "--chat", "01a1-mine",
                         "--scheduled", datetime.now().astimezone().isoformat()]), 1)
                submit.assert_not_called()
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
        self.assertRegex(lines[-1], r"^Codex resume scheduled for \S+ kl\. \S+, or earlier if the limit resets "
                                    r"first \(checked every 30 minutes\) in chat 'Find current project status' "
                                    r"via the Codex queue\.$")
        self.assertEqual(lines[-2], lines[-1])
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
                patch.object(codex_resume, "submit_scheduled") as submit,
                patch.object(codex_resume, "launchctl"),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(codex_resume.main(
                    ["--every", "30", "--used-up", used_up, "--scheduled", deadline.isoformat()]), 0)
        self.assertEqual(readings, [])
        return ([(moment - deadline).total_seconds() / 60 for moment in checks], submit.call_count,
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
                    patch.object(codex_resume, "submit") as submit,
                    patch.object(codex_resume, "launchctl") as launchctl,
                    redirect_stdout(output),
                ):
                    self.assertEqual(codex_resume.main(
                        ["--start-window", "--scheduled", datetime.now().astimezone().isoformat()]), 0)
                    self.assertEqual(codex_resume.main(["--now", "--start-window"]), 0)
                self.assertEqual(submit.call_count, 2 if sends else 0)
                self.assertFalse(plist_path.exists())
                launchctl.assert_called_once()
                expected = ("No 5-hour window is running; sending to start one." if sends
                            else "Not sending: a 5-hour window is already running.")
                lines = output.getvalue().splitlines()
                self.assertRegex(lines[0], r"^\[\d\d\.\d\d\.\d{4} kl\. \d\d\.\d\d\.\d\d\] " + re.escape(expected) + "$")
                if sends:
                    self.assertEqual(lines[-2:], [
                        expected, "Resuming in the open chat via the app's Accessibility controls.",
                    ])
                else:
                    self.assertEqual(lines[-1], expected)
                self.assertEqual(len(lines), 4 if sends else 2)

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
            "Codex resume scheduled for 20.10.2026 kl. 00.00 via the app's Accessibility controls, "
            "only if no 5-hour window is running.",
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
