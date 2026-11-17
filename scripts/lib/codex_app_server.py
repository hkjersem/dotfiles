"""JSON-RPC client for the Codex app server (`codex app-server`), shared by codex-resume and clean-agents.

RejectedError means the app server did nothing: it could not be started, or it answered with an error.
Any other RuntimeError leaves it unknown whether the request ran, as when the server exits or stays silent.
"""

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

CODEX_CLI_PATHS = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
)
TIMEOUT_SECONDS = 20


class RejectedError(RuntimeError):
    """The app server could not be started or answered with an error, so it did nothing."""


def codex_cli():
    for path in CODEX_CLI_PATHS:
        if os.access(path, os.X_OK):
            return str(path)
    found = shutil.which("codex")
    if found is None:
        raise RejectedError("The Codex CLI is unavailable, so its app server cannot be reached.")
    return found


class AppServer:
    """One app server process for several requests; use as a context manager."""

    def __init__(self, client, timeout=None):
        self.timeout = TIMEOUT_SECONDS if timeout is None else timeout
        try:
            self.process = subprocess.Popen([codex_cli(), "app-server"], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError as error:
            raise RejectedError(f"The Codex app server could not be started: {error}") from error
        self.replies, self.next_id = queue.Queue(), 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            # The queue methods are experimental and need this opt-in.
            self.request("initialize", {"clientInfo": {"name": client, "version": "1"},
                                        "capabilities": {"experimentalApi": True}})
            self._send({"method": "initialized"}, "initialized")
        except RuntimeError as error:
            self.close()
            # Nothing was sent beyond the handshake, so no request ran.
            raise RejectedError(str(error)) from error
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if isinstance(reply, dict) and "id" in reply and ("result" in reply or "error" in reply):
                    self.replies.put(reply)
        finally:
            self.replies.put(None)

    def _send(self, message, method):
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except OSError as error:
            # The server may have read the request before it went away, so whether it ran is unknown.
            raise RuntimeError(f"The Codex app server stopped while sending {method}: {error}") from error

    def request(self, method, params=None):
        """Send one request and return its result; input stays open, as the server exits at end of input."""
        self.next_id += 1
        self._send({"id": self.next_id, "method": method, **({"params": params} if params is not None else {})},
                   method)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                reply = self.replies.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError(f"The Codex app server did not answer {method} in time.") from None
            if reply is None:
                self.replies.put(None)
                raise RuntimeError(f"The Codex app server exited without answering {method}.")
            if reply["id"] == self.next_id:
                break
        if "error" in reply:
            error = reply["error"]
            raise RejectedError(f"The Codex app server rejected {method}: "
                                f"{error.get('message', error) if isinstance(error, dict) else error}")
        return reply["result"]

    def paged(self, method, params=None):
        """All `data` items of a cursor-paged list method."""
        params, items = dict(params or {}), []
        while True:
            page = self.request(method, params)
            items.extend(page.get("data") or [])
            if not page.get("nextCursor"):
                return items
            params["cursor"] = page["nextCursor"]

    def close(self):
        self.process.kill()
        self.process.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def request(method, params=None, client="dotfiles", timeout=None):
    """Send one request to a short-lived app server and return its result."""
    with AppServer(client, timeout) as server:
        return server.request(method, params)
