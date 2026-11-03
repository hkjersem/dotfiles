"""Docker cleanup uses mocked native operations, never the live daemon."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "clean-docker.py"
SPEC = importlib.util.spec_from_file_location("clean_docker", SCRIPT)
DOCKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCKER)


class CleanDockerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.endpoint = {"docker": {"Host": "unix:///mock/docker.sock", "SkipTLSVerify": False}}
        self.builder = {"Name": "local", "Driver": "docker", "Nodes": [
            {"Name": "local", "Endpoint": "local", "Status": "running"}]}
        self.ids = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
        self.object_count = "1"
        self.records = [{"Reclaimable": True, "Shared": False, "Type": "regular"},
                        {"Reclaimable": False, "Shared": False, "Type": "regular"}]
        self.stamps = ["2025-11-01T00:00:00.370018178Z", "2025-12-31T00:00:00.810219203+01:00"]
        self.confirm = patch.object(DOCKER, "confirm_cleanup", return_value=True).start()
        self.addCleanup(patch.stopall)
        patch.object(DOCKER.subprocess, "run", side_effect=self.native).start()
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(DOCKER.time, "time", return_value=1767225600).start()  # 1 January 2026, UTC.

    def native(self, command, **kwargs):
        self.calls.append(command)
        args = command[3:] if command[1:2] == ["--context"] else command[1:]
        if args == ["context", "show"]:
            text = "local"
        elif args[:2] == ["context", "inspect"]:
            text = json.dumps(self.endpoint)
        elif args[:2] == ["buildx", "ls"]:
            text = json.dumps(self.builder)
        elif args[:2] == ["system", "df"]:
            text = "\n".join(json.dumps({"Type": kind, "Size": "1GB", "Reclaimable": "500MB",
                                        "TotalCount": self.object_count})
                             for kind in ("Images", "Containers", "Local Volumes", "Build Cache"))
        elif args[:1] == ["buildx"] and "du" in args:
            text = "\n".join(json.dumps(row) for row in self.records)
        elif args[:2] == ["image", "ls"]:
            text = "\n".join(self.ids)
        elif args[:2] == ["image", "inspect"]:
            text = "\n".join(json.dumps(stamp) for stamp in self.stamps)
        elif "prune" in args:
            text = "Total reclaimed space: 123MB"
        else:
            raise AssertionError(f"Unexpected Docker call: {command}")
        return subprocess.CompletedProcess(command, 0, text, "")

    def run_command(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = DOCKER.main(list(args))
        return code, stdout.getvalue()

    def prunes(self):
        return [command for command in self.calls if "prune" in command]

    def test_inspect_reports_storage_and_guidance_without_discovering_cleanup_candidates(self):
        code, text = self.run_command("--inspect")
        self.assertEqual(code, 0, text)
        self.assertNotIn("Dry run", text)
        self.assertIn("Docker context: local", text)
        self.assertIn("Builder: local", text)
        self.assertIn("Local Volumes", text)
        for category, scope in (("Images", "Dangling only"), ("Containers", "Report only"),
                                ("Local Volumes", "Report only"), ("Build Cache", "Unused build cache")):
            self.assertIn(f"\n\n  ▸ {category}\n    1GB total | 500MB reclaimable\n"
                          f"    Cleanup scope: {scope}\n", text)
        self.assertIn("docker --context local system df --verbose", text)
        self.assertIn("docker --context local container ls --all --size", text)
        self.assertIn("docker --context local volume ls", text)
        self.assertIn("docker --context local buildx --builder local du", text)
        self.assertIn("docker --context local volume inspect NAME", text)
        self.assertIn("docker --context local ps --all --filter volume=NAME", text)
        self.assertIn("stopped containers can hold unsaved changes", text)
        self.assertRegex(text, r"  ▸ Overview\n    docker --context local system df --verbose")
        self.assertRegex(text, r"\n\n  ▸ Build cache\n    docker --context local buildx")
        self.assertRegex(text, r"\n\n  ▸ Containers\n    docker --context local container ls")
        self.assertRegex(text, r"\n\n  ▸ Volumes\n    docker --context local volume ls")
        self.assertIn("Remove one (no prompt, no --force):", text)
        self.assertIn("Remove one (no prompt):", text)
        self.assertIn("Freed space may remain inside Docker's virtual disk.", text)
        self.assertNotIn("Cleanup plan", text)
        self.assertNotIn("Cleanup results", text)
        self.assertFalse(any("du" in call or "image" in call for call in self.calls))
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_dry_run_shows_only_plan_and_relevant_age_warnings(self):
        code, text = self.run_command("--dry-run", "--older-than", "30d")
        self.assertEqual(code, 0, text)
        self.assertTrue(text.startswith("Dry run"))
        self.assertIn("Cleanup plan", text)
        self.assertIn("up to 1 unused private records before age filtering", text)
        self.assertRegex(text, r"\n\n  ▸ Dangling images\n    up to 1 candidates")
        self.assertIn("local image-record creation", text)
        self.assertNotIn("Docker storage", text)
        self.assertNotIn("Inspect further", text)
        self.assertNotIn("Cleanup results", text)
        self.assertFalse(any("df" in call for call in self.calls))
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_selected_prunes_use_pinned_targets_and_native_age_semantics(self):
        code, text = self.run_command("--only", "build-cache,dangling-images", "--older-than", "30d")
        self.assertEqual(code, 0, text)
        self.confirm.assert_called_once_with("Clean build-cache, dangling-images older than 30d?")
        self.assertEqual(self.prunes(), [
            ["docker", "--context", "local", "buildx", "--builder", "local", "prune",
             "--force", "--filter", "until=2592000s"],
            ["docker", "--context", "local", "image", "prune", "--force",
             "--filter", "until=1764633600"],
        ])
        self.assertIn("Total reclaimed space: 123MB", text)
        self.assertIn("Cleanup results", text)
        self.assertNotIn("Cleanup plan", text)
        self.assertNotIn("Docker storage", text)
        self.assertNotIn("Inspect further", text)
        self.assertNotIn("candidates", text)

    def test_normal_run_does_not_render_plan_before_confirmation(self):
        def confirm(prompt):
            before = stdout.getvalue()
            self.assertIn("Docker context: local", before)
            self.assertNotIn("Cleanup plan", before)
            self.assertNotIn("Cleanup results", before)
            self.assertNotIn("unused private records", before)
            self.assertEqual(self.prunes(), [])
            return True

        self.confirm.side_effect = confirm
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = DOCKER.main(["--only", "build-cache"])
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertEqual(len(self.prunes()), 1)
        self.assertIn("Cleaned", stdout.getvalue())

    def test_image_only_never_touches_builder_and_retains_tagged_images(self):
        code, text = self.run_command("--only", "dangling-images")
        self.assertEqual(code, 0, text)
        self.assertFalse(any("buildx" in call for call in self.calls))
        self.assertEqual(self.prunes(), [["docker", "--context", "local", "image", "prune", "--force"]])
        self.assertTrue(any("dangling=true" in call for call in self.calls))

    def test_build_cache_excludes_records_outside_default_prune_scope(self):
        self.records = [
            {"Reclaimable": True, "Shared": True, "Type": "regular"},
            {"Reclaimable": True, "Shared": False, "Type": "internal"},
            {"Reclaimable": True, "Shared": False, "Type": "frontend"},
            {"Reclaimable": False, "Shared": False, "Type": "regular"},
        ]
        code, text = self.run_command("--only", "build-cache", "--older-than", "30d")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing eligible to clean.", text)
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_cache_age_preview_is_explicitly_unfiltered_not_rounded_last_use(self):
        self.records[0]["LastUsedAt"] = "5 minutes ago"
        code, text = self.run_command("--only", "build-cache", "--older-than", "30d", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("before age filtering", text)
        self.assertIn("Exact last-use timestamps are unavailable", text)
        du = next(call for call in self.calls if "du" in call)
        self.assertNotIn("--filter", du)
        self.confirm.assert_not_called()

    def test_image_timestamp_fraction_lengths_are_portable(self):
        for fraction in ("", ".1", ".12", ".123", ".1234", ".12345", ".123456",
                         ".1234567", ".12345678", ".123456789"):
            with self.subTest(fraction=fraction):
                self.stamps = [f"2025-11-01T00:00:00{fraction}Z",
                               f"2025-12-31T00:00:00{fraction}+01:00"]
                code, text = self.run_command("--only", "dangling-images", "--older-than", "30d",
                                              "--dry-run")
                self.assertEqual(code, 0, text)
                self.assertIn("up to 1 candidates", text)

    def test_explicit_context_and_builder_override_environment_and_default_selection(self):
        self.builder["Name"] = "remote-builder"
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://other:2375", "BUILDX_BUILDER": "other"}):
            code, text = self.run_command("--context", "named", "--builder", "remote-builder",
                                          "--only", "build-cache")
        self.assertEqual(code, 0, text)
        self.assertIn("Builder: remote-builder", text)
        self.assertEqual(self.prunes()[0][:7],
                         ["docker", "--context", "named", "buildx", "--builder", "remote-builder", "prune"])
        self.assertFalse(any(call[1:] == ["context", "show"] for call in self.calls))

    def test_selected_buildx_builder_does_not_override_context_builder(self):
        with patch.dict(os.environ, {"BUILDX_BUILDER": "elsewhere"}):
            code, text = self.run_command("--only", "build-cache")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.prunes()[0][3:6], ["buildx", "--builder", "local"])

    def test_inspection_hints_quote_explicit_targets(self):
        self.builder["Name"] = "builder with space"
        code, text = self.run_command("--inspect", "--context", "context with space",
                                      "--builder", "builder with space")
        self.assertEqual(code, 0, text)
        self.assertIn("docker --context 'context with space' system df --verbose", text)
        self.assertIn("buildx --builder 'builder with space' du", text)

    def test_empty_categories_do_not_show_removal_guidance(self):
        self.object_count = "0"
        code, text = self.run_command("--inspect")
        self.assertEqual(code, 0, text)
        self.assertNotIn("Cleanup results", text)
        self.assertNotIn("container rm", text)
        self.assertNotIn("volume rm", text)
        self.assertNotIn("volume inspect", text)
        self.assertNotIn("Manual removal", text)

    def test_help_keeps_manual_cleanup_guidance_in_the_report(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as caught:
                DOCKER.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--older-than", stdout.getvalue())
        self.assertNotIn("volume inspect", stdout.getvalue())
        self.assertNotIn("container rm", stdout.getvalue())
        self.assertEqual(self.calls, [])

    def test_docker_host_requires_explicit_named_context(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://other:2375"}):
            code, text = self.run_command("--only", "build-cache")
        self.assertEqual(code, 1, text)
        self.assertIn("select a named --context", text)
        self.assertEqual(self.calls, [])
        self.confirm.assert_not_called()

    def test_declining_or_no_candidates_does_not_prune(self):
        self.confirm.return_value = False
        code, text = self.run_command("--only", "build-cache")
        self.assertEqual(code, 0, text)
        self.assertIn("Cancelled; nothing cleaned.", text)
        self.assertNotIn("Cleanup results", text)
        self.assertEqual(self.prunes(), [])
        self.ids = []
        self.confirm.reset_mock()
        code, text = self.run_command("--only", "dangling-images")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing eligible to clean.", text)
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_endpoint_and_builder_changes_after_confirmation_stop_deletion(self):
        for change in ("context", "builder"):
            with self.subTest(change=change):
                self.calls.clear()
                self.endpoint["docker"]["Host"] = "unix:///mock/docker.sock"
                self.builder["Driver"] = "docker"

                def confirm(prompt):
                    if change == "context":
                        self.endpoint["docker"]["Host"] = "tcp://different:2375"
                    else:
                        self.builder["Driver"] = "docker-container"
                    return True

                self.confirm.side_effect = confirm
                code, text = self.run_command("--only", "build-cache")
                self.assertEqual(code, 1, text)
                self.assertIn("changed after checks", text)
                self.assertEqual(self.prunes(), [])

    def test_stopped_or_missing_builders_fail_before_confirmation(self):
        for change in ("stopped", "missing"):
            with self.subTest(change=change):
                self.builder["Nodes"][0]["Status"] = "stopped" if change == "stopped" else "running"
                self.builder["Name"] = "other" if change == "missing" else "local"
                code, text = self.run_command("--only", "build-cache")
                self.assertEqual(code, 1, text)
                self.assertEqual(self.prunes(), [])
                self.confirm.assert_not_called()

    def test_invalid_build_cache_report_blocks_normal_run_before_confirmation(self):
        self.records = [{"Reclaimable": True}]
        code, text = self.run_command("--only", "build-cache,dangling-images")
        self.assertEqual(code, 1, text)
        self.assertIn("invalid build-cache report", text)
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_dry_run_selection_does_not_query_unselected_cleaner(self):
        code, text = self.run_command("--dry-run", "--only", "dangling-images")
        self.assertEqual(code, 0, text)
        self.assertIn("Dangling images", text)
        self.assertNotIn("Build cache", text)
        self.assertFalse(any("buildx" in call or "df" in call for call in self.calls))
        self.confirm.assert_not_called()
        self.assertEqual(self.prunes(), [])

    def test_query_errors_and_malformed_reports_block_every_cleaner(self):
        original = self.native
        for bad in ("daemon unavailable", "invalid JSON", "empty report", "invalid timestamp"):
            with self.subTest(bad=bad):
                self.calls.clear()

                def native(command, **kwargs):
                    if "df" in command and bad == "daemon unavailable":
                        return subprocess.CompletedProcess(command, 1, "", "daemon unavailable")
                    if "df" in command and bad == "invalid JSON":
                        return subprocess.CompletedProcess(command, 0, "not json", "")
                    if "df" in command and bad == "empty report":
                        return subprocess.CompletedProcess(command, 0, "", "")
                    if "inspect" in command and "image" in command and bad == "invalid timestamp":
                        return subprocess.CompletedProcess(command, 0, '"bad"\n"bad"', "")
                    return original(command, **kwargs)

                DOCKER.subprocess.run.side_effect = native
                args = (("--only", "build-cache,dangling-images", "--older-than", "7d")
                        if bad == "invalid timestamp" else ("--inspect",))
                code, text = self.run_command(*args)
                self.assertEqual(code, 1, text)
                self.assertEqual(self.prunes(), [])
                self.confirm.assert_not_called()

    def test_failed_prune_is_not_success_and_stops_remaining_cleanup(self):
        def native(command, **kwargs):
            if "prune" in command:
                self.calls.append(command)
                return subprocess.CompletedProcess(command, 1, "", "permission denied")
            return self.native(command, **kwargs)

        DOCKER.subprocess.run.side_effect = native
        code, text = self.run_command("--only", "build-cache,dangling-images")
        self.assertEqual(code, 1, text)
        self.assertIn("permission denied", text)
        self.assertNotIn("Cleaned", text)
        self.assertEqual(len(self.prunes()), 1)

    def test_help_and_invalid_selection_never_call_docker(self):
        for args, expected in ((["--help"], 0), ([], 2), (["--only", "volumes"], 2),
                               (["--only", "containers"], 2), (["--older-than", "7"], 2),
                               (["--only", "build-cache", "--older-than", "7"], 2),
                               (["--inspect", "--dry-run"], 2),
                               (["--inspect", "--only", "build-cache"], 2),
                               (["--inspect", "--older-than", "7d"], 2),
                               (["--only", "dangling-images", "--builder", "other"], 2)):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    DOCKER.main(args)
                self.assertEqual(caught.exception.code, expected)
        self.assertEqual(self.calls, [])


class DockerConfirmationTests(unittest.TestCase):
    def test_closed_input_declines_shared_confirmation(self):
        run = subprocess.run
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": home}), \
                    patch.object(DOCKER.subprocess, "run", side_effect=lambda command: run(
                        command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)):
                self.assertFalse(DOCKER.confirm_cleanup())

    def test_confirmation_failure_is_reported(self):
        with patch.object(DOCKER.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaisesRegex(OSError, "confirmation failed with status 7"):
                DOCKER.confirm_cleanup()


if __name__ == "__main__":
    unittest.main()
