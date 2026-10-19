"""Offline regression tests for codex-resume scheduling."""

from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import plistlib
import re
import shutil
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
            for action in ("--scheduled-initial-submit", "--scheduled-submit"):
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
            call("--scheduled-initial-submit", None), call("--scheduled-submit", None),
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
            [call("--scheduled-initial-submit", None)] + [call("--scheduled-submit", None)] * 10,
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
                submit.assert_called_once_with("--scheduled-initial-submit", None)
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
            ("--inspect", call("--inspect")), ("--check", call("--check")),
            ("--now", call("--submit", None)),
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
                patch.object(codex_resume, "next_occurrence",
                              return_value=codex_resume.next_occurrence("16:30", now)),
                patch.object(codex_resume, "launchctl") as launchctl,
                patch("builtins.print"),
            ):
                codex_resume.schedule("16:30")

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
                    codex_resume.schedule("25:00")
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
                    codex_resume.schedule("16:30")
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
            submit.assert_called_once_with("--scheduled-initial-submit", None)
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
                call("--scheduled-initial-submit", "Resume now"),
                call("--scheduled-submit", "Resume now"),
            ])

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
