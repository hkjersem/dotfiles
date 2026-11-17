"""Process metadata is shared without weakening caller failure policies."""

import importlib.util
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/lib"))
import processes


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProcessTests(unittest.TestCase):
    def listing(self, stdout, returncode=0, stderr=""):
        return patch.object(processes.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], returncode, stdout, stderr,
        ))

    def test_nearest_ancestor_and_session_start_use_the_same_metadata(self):
        awake = load_script("agent-awake")
        sessions = load_script("agent-sessions")
        listing = (
            "1 0 Mon Nov  9 08:00:00 2026 /sbin/launchd\n"
            "20 1 Mon Nov  9 09:00:00 2026 /opt/copilot\n"
            "30 20 Mon Nov  9 10:00:00 2026 /path with spaces/claude\n"
            "40 30 Mon Nov  9 10:00:01 2026 /bin/bash\n"
        )
        with self.listing(listing), patch.object(awake.os, "getppid", return_value=40):
            self.assertEqual(awake.agent_process(("claude", "copilot")), 30)
            self.assertEqual(awake.agent_process(("copilot",)), 20)
            self.assertIsNone(awake.agent_process(("codex",)))
            self.assertEqual(sessions.process_starts()["30"], "Mon Nov 9 10:00:00 2026")

    def test_failed_or_incomplete_listing_is_not_an_empty_snapshot(self):
        awake = load_script("agent-awake")
        sessions = load_script("agent-sessions")
        valid = "20 1 Mon Nov 9 09:00:00 2026 /opt/copilot\n"
        for stdout, status, stderr in (
                ("", 0, ""), (valid, 1, ""), (valid, 0, "permission denied"),
                (valid + "unparseable\n", 0, ""), (valid + valid, 0, ""),
        ):
            with self.subTest(stdout=stdout, status=status, stderr=stderr), self.listing(stdout, status, stderr):
                with self.assertRaises(OSError):
                    processes.process_snapshot()
                with self.assertRaises(OSError):
                    sessions.process_starts()
                self.assertIsNone(awake.agent_process(("copilot",)))

    def test_timeout_is_visible_to_reporter_but_best_effort_for_hooks(self):
        awake = load_script("agent-awake")
        sessions = load_script("agent-sessions")
        with patch.object(processes.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            with self.assertRaises(subprocess.TimeoutExpired):
                processes.process_snapshot()
            with self.assertRaises(subprocess.TimeoutExpired):
                sessions.process_starts()
            with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"XDG_STATE_HOME": root}):
                directory = Path(root) / "agent-sessions"
                directory.mkdir()
                (directory / "12345678-1234-1234-1234-123456789abc").write_text("fixture\n")
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(sessions.main([]), 1)
                self.assertIn("could not read session restoration status", error.getvalue())
            self.assertIsNone(awake.agent_process(("copilot",)))


if __name__ == "__main__":
    unittest.main()
