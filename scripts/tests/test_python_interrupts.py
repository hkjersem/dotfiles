"""Interrupt real command entry points without running their actions."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
PYTHON = "/usr/bin/python3"
HARNESS = """
import os
import runpy
import signal
import sys

script, source, function = sys.argv[1:4]
sys.argv = [script, *sys.argv[4:]]
sys.stderr.isatty = lambda: True

def interrupt(frame, event, arg):
    if (event == "call" and frame.f_code.co_filename == source
            and frame.f_code.co_name == function):
        os.kill(os.getpid(), signal.SIGINT)

sys.settrace(interrupt)
runpy.run_path(script, run_name="__main__")
"""


class PythonInterruptTests(unittest.TestCase):
    def test_authored_python_commands_exit_cleanly_on_sigint(self):
        scripts = sorted((REPO / "scripts").glob("*.py"))
        scripts += sorted((REPO / "agents/skills").glob("*/scripts/*.py"))
        with tempfile.TemporaryDirectory(prefix="python-interrupt-test-") as home:
            env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
            for script in scripts:
                with self.subTest(script=script.relative_to(REPO)):
                    result = subprocess.run(
                        [PYTHON, "-B", "-c", HARNESS, str(script), str(script), "main"],
                        cwd=home, env=env, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertIn("interrupted", result.stderr.lower())

    def test_interrupted_roadmap_validation_clears_terminal_progress(self):
        skill = REPO / "agents/skills/roadmap/scripts/roadmap.py"
        script = REPO / "scripts/roadmaps.py"
        with tempfile.TemporaryDirectory(prefix="roadmap-interrupt-test-") as home:
            root = Path(home) / "repo"
            root.mkdir()
            env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
            subprocess.run(
                [PYTHON, "-B", str(skill), "init", "--root", str(root)],
                env=env, capture_output=True, check=True,
            )
            result = subprocess.run(
                [PYTHON, "-B", "-c", HARNESS, str(script), str(skill), "check_report",
                 "--check", str(root)],
                cwd=home, env=env, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
            self.assertIn(b"Checking ~/repo", result.stderr)
            self.assertTrue(result.stderr.endswith(b"\r\x1b[K\nInterrupted.\n"), result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
