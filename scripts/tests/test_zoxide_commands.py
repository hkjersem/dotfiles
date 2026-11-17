"""Directory lookup shortcuts never act after a failed or cancelled lookup."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_command_help import Sandbox


class ZoxideCommandsTests(unittest.TestCase):
    def run_shortcut(self, name, args=(), query_status=0, target_status=0,
                     query_output=None, target_stderr="", missing=()):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(Path(root).resolve())
            directory = sandbox.project / "folder with spaces"
            directory.mkdir()
            tools = sandbox.root / "bin"
            for tool in ("zoxide", "ls", "code", "open"):
                if tool in missing:
                    continue
                status = query_status if tool == "zoxide" else target_status
                output = str(directory) + "\n" if query_output is None else query_output
                program = (
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    f"with open({str(sandbox.calls)!r}, 'a') as calls:\n"
                    f"    calls.write(json.dumps([{tool!r}, sys.argv[1:], os.getcwd()]) + '\\n')\n"
                )
                if tool == "zoxide":
                    program += f"sys.stdout.write({output!r})\n"
                    if status:
                        program += "sys.stderr.write('lookup failed or cancelled\\n')\n"
                else:
                    program += f"sys.stderr.write({target_stderr!r})\n"
                program += f"sys.exit({status})\n"
                executable = tools / tool
                executable.write_text(program)
                executable.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", "-c",
                 'source "$HOME/.aliases"\n"$@"', "shortcut", name, *args],
                cwd=sandbox.project, env=sandbox.env,
                capture_output=True, text=True, timeout=30,
            )
            calls = [json.loads(line) for line in sandbox.tool_calls().splitlines()]
            return result, calls, str(directory), str(sandbox.project)

    def test_keywords_select_one_directory_without_changing_cwd(self):
        for name, tool, flags in (
            ("zla", "ls", ["-lAh", "--"]),
            ("zcode", "code", ["--"]),
            ("zopen", "open", ["--"]),
            ("zfork", "open", ["-a", "Fork", "--"]),
        ):
            with self.subTest(name=name):
                result, calls, directory, cwd = self.run_shortcut(name, ["front", "end"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [
                    ["zoxide", ["query", "--", "front", "end"], cwd],
                    [tool, [*flags, directory], cwd],
                ])

    def test_no_keywords_uses_interactive_picker(self):
        result, calls, directory, cwd = self.run_shortcut("zopen")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            ["zoxide", ["query", "--interactive", "--"], cwd],
            ["open", ["--", directory], cwd],
        ])

    def test_no_match_and_cancellation_never_run_target(self):
        for name in ("zla", "zcode", "zopen", "zfork"):
            for status, args in ((1, ["missing"]), (130, [])):
                with self.subTest(name=name, status=status):
                    result, calls, _, _ = self.run_shortcut(
                        name, args, query_status=status)
                    self.assertEqual(result.returncode, status)
                    self.assertIn("lookup failed or cancelled", result.stderr)
                    self.assertEqual([call[0] for call in calls], ["zoxide"])

    def test_invalid_query_output_never_runs_target(self):
        for output in ("", "relative/path\n", "/nonexistent-zoxide-command-test\n"):
            with self.subTest(output=output):
                result, calls, _, _ = self.run_shortcut("zcode", ["front"], query_output=output)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error: zoxide did not return", result.stderr)
                self.assertEqual([call[0] for call in calls], ["zoxide"])

    def test_help_and_unknown_flags_never_run_tools(self):
        for name in ("zla", "zcode", "zopen", "zfork"):
            for args, status in ((["--help"], 0), (["--unknown"], 2)):
                with self.subTest(name=name, args=args):
                    result, calls, _, _ = self.run_shortcut(name, args)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(calls, [])
                    if status == 0:
                        self.assertIn(f"Usage: {name}", result.stdout)

    def test_target_failure_is_preserved(self):
        result, calls, _, _ = self.run_shortcut("zcode", ["front"], target_status=7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual([call[0] for call in calls], ["zoxide", "code"])

    def test_missing_zoxide_reports_error_without_running_target(self):
        result, calls, _, _ = self.run_shortcut("zcode", ["front"], missing=("zoxide",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stderr)
        self.assertIn("zoxide", result.stderr)
        self.assertEqual(calls, [])

    def test_missing_fork_reports_native_error_without_fallback(self):
        message = "Unable to find application named 'Fork'\n"
        result, calls, directory, cwd = self.run_shortcut(
            "zfork", ["front"], target_status=1, target_stderr=message)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, message)
        self.assertEqual(calls, [
            ["zoxide", ["query", "--", "front"], cwd],
            ["open", ["-a", "Fork", "--", directory], cwd],
        ])
