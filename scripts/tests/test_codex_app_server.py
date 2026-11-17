"""The shared Codex app-server client, against a fake `codex app-server`."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import codex_app_server  # noqa: E402

FAKE_APP_SERVER = """#!/usr/bin/env python3
import json, sys
mode = "{mode}"
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["method"] == "initialize":
        if mode == "init-error":
            print(json.dumps({{"id": request["id"], "error": {{"message": "bad client"}}}}), flush=True)
            continue
        experimental = request["params"].get("capabilities", {{}}).get("experimentalApi")
        print(json.dumps({{"id": request["id"], "result": {{}}}}), flush=True)
    elif mode == "exit":
        sys.exit(0)
    elif mode == "silent":
        continue
    elif mode == "error":
        print(json.dumps({{"id": request["id"], "error": {{"message": "not signed in"}}}}), flush=True)
    else:
        params = request.get("params")
        if mode == "stale":
            print(json.dumps({{"id": request["id"] - 1, "result": "stale"}}), flush=True)
        if request["method"] == "list":
            cursor = (params or {{}}).get("cursor")
            page = {{"data": [cursor or "first"], "nextCursor": None if cursor else "second"}}
            print(json.dumps({{"id": request["id"], "result": page}}), flush=True)
            continue
        print("not json", flush=True)
        print(json.dumps({{"id": request["id"], "result": {{"id": request["id"], "method": request["method"],
                          "params": params, "experimentalApi": experimental}}}}), flush=True)
"""


class AppServerTests(unittest.TestCase):
    def fake_cli(self, mode):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cli = Path(temporary.name) / "codex"
        cli.write_text(FAKE_APP_SERVER.format(mode=mode))
        cli.chmod(0o755)
        patcher = patch.object(codex_app_server, "CODEX_CLI_PATHS", (cli,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_request_waits_for_its_reply_with_experimental_api(self):
        self.fake_cli("answer")
        self.assertEqual(codex_app_server.request("thread/queue/list", {"threadId": "01a1"}),
                         {"id": 2, "method": "thread/queue/list", "params": {"threadId": "01a1"},
                          "experimentalApi": True})

    def test_replies_to_other_requests_are_skipped(self):
        self.fake_cli("stale")
        self.assertEqual(codex_app_server.request("a")["method"], "a")

    def test_one_server_answers_several_requests_and_pages(self):
        self.fake_cli("answer")
        with codex_app_server.AppServer("test") as server:
            self.assertEqual(server.request("a")["id"], 2)
            self.assertEqual(server.request("b", {"x": 1})["params"], {"x": 1})
            self.assertEqual(server.paged("list"), ["first", "second"])
        self.assertIsNotNone(server.process.returncode)

    def test_errors_distinguish_rejected_from_unknown_outcome(self):
        self.fake_cli("error")
        with self.assertRaisesRegex(codex_app_server.RejectedError, "rejected account/rateLimits/read: not signed in"):
            codex_app_server.request("account/rateLimits/read")
        self.fake_cli("init-error")
        with self.assertRaisesRegex(codex_app_server.RejectedError, "rejected initialize: bad client"):
            codex_app_server.request("thread/queue/add")
        self.fake_cli("exit")
        with self.assertRaisesRegex(RuntimeError, "exited without answering") as error:
            codex_app_server.request("account/rateLimits/read")
        self.assertNotIsInstance(error.exception, codex_app_server.RejectedError)
        self.fake_cli("silent")
        with self.assertRaisesRegex(RuntimeError, "did not answer") as error:
            codex_app_server.request("account/rateLimits/read", timeout=0.5)
        self.assertNotIsInstance(error.exception, codex_app_server.RejectedError)

    def test_missing_or_unstartable_cli_is_rejected(self):
        with (
            patch.object(codex_app_server, "CODEX_CLI_PATHS", ()),
            patch.object(codex_app_server.shutil, "which", return_value=None),
            self.assertRaisesRegex(codex_app_server.RejectedError, "Codex CLI is unavailable"),
        ):
            codex_app_server.request("account/rateLimits/read")
        with (
            patch.object(codex_app_server, "codex_cli", return_value="/nonexistent/codex"),
            self.assertRaisesRegex(codex_app_server.RejectedError, "could not be started"),
        ):
            codex_app_server.request("account/rateLimits/read")

    def test_broken_pipe_after_the_handshake_is_an_unknown_outcome(self):
        process = MagicMock()
        process.stdout = iter([json.dumps({"id": 1, "result": {}}) + "\n"])
        # initialize and initialized are written; the request itself hits a closed pipe.
        process.stdin.write.side_effect = [None, None, BrokenPipeError(32, "Broken pipe")]
        with (
            patch.object(codex_app_server, "codex_cli", return_value="codex"),
            patch.object(codex_app_server.subprocess, "Popen", return_value=process),
            self.assertRaisesRegex(RuntimeError, "stopped while sending thread/queue/add") as error,
        ):
            codex_app_server.request("thread/queue/add", {"threadId": "01a1-mine"})
        self.assertNotIsInstance(error.exception, codex_app_server.RejectedError)
        process.kill.assert_called_once()

    def test_broken_pipe_during_the_handshake_is_rejected(self):
        process = MagicMock()
        process.stdout = iter(())
        process.stdin.write.side_effect = BrokenPipeError(32, "Broken pipe")
        with (
            patch.object(codex_app_server, "codex_cli", return_value="codex"),
            patch.object(codex_app_server.subprocess, "Popen", return_value=process),
            self.assertRaises(codex_app_server.RejectedError),
        ):
            codex_app_server.request("thread/queue/add")
        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
