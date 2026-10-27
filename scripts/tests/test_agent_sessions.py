"""Tests for the copilot/cplt wrappers and session resuming in zsh/conf.d/agent-sessions.zsh."""

import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import struct
import tempfile
import termios
import time
import unittest
import uuid


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "zsh/conf.d/agent-sessions.zsh"
# Logs its arguments. Like Copilot, it logs each session it registers and
# locks the sessions it creates or writes to; like Claude Code, it keeps the
# session it has open in sessions/<pid>.json and a transcript per session.
STUB = """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
# Like Copilot and Claude Code, shut down cleanly when the terminal closes.
signal.signal(signal.SIGHUP, lambda *_: (time.sleep(0.3), sys.exit(int(os.environ.get("AGENT_TEST_HUP_STATUS", "0")))))
if Path(sys.argv[0]).name == "cplt" and args == ["--help"]:
    print('''Options:
      --agent <AGENT>
      --preset <PRESET>
  -d, --project-dir <DIR>
      --repo-dir <DIR>
      --name <SESSION>
      --allow-write-state <PATH>
      --future-option <VALUE>
      --pass-env <VAR>
      --resume[=<SESSION>]
      --continue
''')
    sys.exit(0)
# AGENT_TEST_CPLT_AGENT is the sandbox.agent value in cplt's config.
if Path(sys.argv[0]).name == "cplt" and args[:3] == ["config", "get", "sandbox.agent"]:
    print(os.environ.get("AGENT_TEST_CPLT_AGENT", ""))
    sys.exit(0)
with open(os.environ["AGENT_TEST_LOG"], "a") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, os.getcwd()] + args) + "\\n")
if os.environ.get("AGENT_TEST_FAIL_EARLY"):
    sys.exit(3)
session = None
for i, arg in enumerate(args):
    if arg in ("--session-id", "--resume") and i + 1 < len(args):
        session = args[i + 1]
    elif arg.startswith(("--session-id=", "--resume=")):
        session = arg.split("=", 1)[1]
# AGENT_TEST_SWITCH lists sessions switched to with /new, /clear or /resume.
switches = [s for s in os.environ.get("AGENT_TEST_SWITCH", "").split(",") if s]
agent = Path(sys.argv[0]).name
if agent == "cplt":
    separator = args.index("--") if "--" in args else len(args)
    for key in ("DOTFILES_AGENT_SESSION", "DOTFILES_AGENT_KIND"):
        if not any(args[i:i + 2] == ["--pass-env", key] for i in range(separator)):
            os.environ.pop(key, None)
    agent = os.environ.get("AGENT_TEST_CPLT_AGENT") or "copilot"
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if arg == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
if agent == "claude":
    home = Path(os.environ["CLAUDE_CONFIG_DIR"])
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    for i, target in enumerate(([session] if session else []) + switches):
        if i:
            time.sleep(1.1)
        target = session if target == "LAUNCHED" else target
        (home / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": target}))
        if not os.environ.get("AGENT_TEST_EMPTY"):
            (home / "projects/-project").mkdir(parents=True, exist_ok=True)
            (home / "projects/-project" / f"{target}.jsonl").touch()
    if os.environ.get("AGENT_TEST_HANG"):
        time.sleep(30)
    (home / "sessions" / f"{os.getpid()}.json").unlink(missing_ok=True)
    sys.exit(int(os.environ.get("AGENT_TEST_STATUS", "0")))
home = Path(os.environ["COPILOT_HOME"])
(home / "logs").mkdir(parents=True, exist_ok=True)
process_log = home / "logs" / f"process-{int(time.time() * 1000)}-{os.getpid()}.log"
for i, target in enumerate(([session] if session else []) + switches):
    if i:
        time.sleep(1.1)
    target = session if target == "LAUNCHED" else target
    # AGENT_TEST_NO_LOG acts like Copilot versions that do not log this line.
    if not os.environ.get("AGENT_TEST_NO_LOG"):
        with process_log.open("a") as log:
            log.write(f"2026-01-01T00:00:00.000Z [INFO] Registering foreground session: {target}\\n")
    folder = home / "session-state" / target
    lock = folder / f"inuse.{os.getpid()}.lock"
    # Resuming a session without writing to it does not lock it.
    if i and (folder / "events.jsonl").exists() and not lock.exists():
        continue
    folder.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        with open(folder / "events.jsonl", "a") as events:
            events.write('{"type":"session.resume"}\\n')
        continue
    if not os.environ.get("AGENT_TEST_EMPTY"):
        (folder / "events.jsonl").write_text("{}\\n")
    lock.touch()
if os.environ.get("AGENT_TEST_HANG"):
    time.sleep(float(os.environ.get("AGENT_TEST_HANG_SECONDS", "30")))
if os.environ.get("AGENT_TEST_INPUT"):
    print("AGENT_INPUT_READY", flush=True)
    Path(os.environ["AGENT_TEST_INPUT"]).write_text(input())
sys.exit(int(os.environ.get("AGENT_TEST_STATUS", "0")))
"""
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ID_A = "0f6a2b4c-1d3e-4f50-8a9b-0c1d2e3f4a5b"
ID_B = "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"
CONTEXT = ["--pass-env", "DOTFILES_AGENT_SESSION", "--pass-env", "DOTFILES_AGENT_KIND"]


@unittest.skipUnless(shutil.which("zsh"), "zsh is required")
class AgentSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in ("cplt", "copilot", "claude"):
            (self.bin / name).write_text(STUB)
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "log.jsonl"
        self.sessions = self.tmp / "state/agent-sessions"
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.other = self.tmp / "other"
        self.other.mkdir()
        zdotdir = self.tmp / "zdotdir"
        zdotdir.mkdir()
        (zdotdir / ".zshrc").write_text(f"PS1='%# '\nsource {MODULE}\n")
        self.env = {
            "HOME": str(self.tmp),
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "XDG_STATE_HOME": str(self.tmp / "state"),
            "ZDOTDIR": str(zdotdir),
            "AGENT_TEST_LOG": str(self.log),
            "TERM": "dumb",
            "COPILOT_HOME": str(self.tmp / "copilot"),
            "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
            "AGENT_SESSION_FOLLOW_INTERVAL": "0.1",
        }

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def args(self):
        return [[call[0]] + call[2:] for call in self.calls()]

    def markers(self):
        return sorted(p.name for p in self.sessions.iterdir()) if self.sessions.exists() else []

    def record(self, session_id):
        """The record of a launch of session_id, or None."""
        for name in self.markers():
            path = self.sessions / name
            if "." not in name and path.read_text().split("\n")[5] == session_id:
                return path
        return None

    def recorded(self):
        """Session IDs that have a record, with ".current" for followed ones."""
        result = []
        for name in self.markers():
            if "." not in name:
                session_id = (self.sessions / name).read_text().split("\n")[5]
                result.append(session_id)
                if (self.sessions / f"{name}.current").exists():
                    result.append(f"{session_id}.current")
        return sorted(result)

    def currents(self):
        return [(self.sessions / name).read_text().strip() for name in self.markers() if name.endswith(".current")]

    def current(self, session_id):
        record = self.record(session_id)
        current = record and record.with_name(f"{record.name}.current")
        return current.read_text().strip() if current and current.exists() else None

    def run_zsh(self, script, **env):
        result = subprocess.run(
            ["zsh", "-f", "-c", f"source {MODULE}\n{script}"],
            cwd=self.project, env={**self.env, **env},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def interactive_shell(self, cwd, leader=None, size=None, **env):
        """With leader, zsh runs under that sh command, as iTerm2 runs it under login."""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            if size:
                set_size(0, *size)
            if leader:
                os.execve("/bin/sh", ["sh", "-c", f"{leader} /bin/zsh -d -i; :"], {**self.env, **env})
            os.execve("/bin/zsh", ["zsh", "-d", "-i"], {**self.env, **env})
        # pty.fork leaves the master inheritable; a later shell holding it
        # would keep this terminal open after hang_up closes it.
        os.set_inheritable(fd, False)
        self.addCleanup(self._reap, pid, fd)
        return pid, fd

    def _reap(self, pid, fd):
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    def drain(self, fd, done, timeout=10):
        """Read the pty until done(output) is true; macOS blocks exit on unread output."""
        deadline = time.time() + timeout
        output = b""
        while time.time() < deadline:
            if done(output):
                return output
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    output += os.read(fd, 4096)
                except OSError:
                    pass
        self.fail(f"timed out: {output.decode(errors='replace')}")

    def wait_for_calls(self, fd, count):
        self.drain(fd, lambda _: len(self.calls()) >= count)
        self.assertEqual(len(self.calls()), count, self.calls())

    def exit_shell(self, pid, fd):
        os.write(fd, b"exit\n")
        self.drain(fd, lambda _: os.waitpid(pid, os.WNOHANG)[0] != 0)

    def open_and_close(self, cwd, **env):
        pid, fd = self.interactive_shell(cwd, **env)
        self.exit_shell(pid, fd)

    def hang_up(self, pid, fd, timeout=10):
        os.close(fd)
        deadline = time.time() + timeout
        while os.waitpid(pid, os.WNOHANG)[0] == 0:
            if time.time() > deadline:
                self.fail("shell did not exit after its terminal closed")
            time.sleep(0.05)

    def write_marker(self, session_id, cwd, launcher="cplt", args="", age=0, pid=99999999, started="",
                     events=True, agent="copilot", app=None):
        if events and agent == "claude":
            folder = self.tmp / "claude/projects/-project"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{session_id}.jsonl").touch()
        elif events:
            folder = self.tmp / "copilot/session-state" / session_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "events.jsonl").write_text("{}\n")
        self.sessions.mkdir(parents=True, exist_ok=True)
        marker = self.sessions / str(uuid.uuid4())
        marker.write_text(f"{cwd}\n{launcher}\n{args}\n{pid}\n{started}\n{session_id}\n{agent}\n")
        if app is not None:
            marker.write_text(marker.read_text() + f"{app}\n")
        stamp = time.time() - age
        os.utime(marker, (stamp, stamp))
        return marker

    def write_snapshot(self, marker, pid, started, sampled):
        snapshot = marker.with_name(f"{marker.name}.pids")
        snapshot.write_text(f"{sampled}\n{pid} {started}\n")
        os.utime(snapshot, (sampled, sampled))
        return snapshot

    def test_restore_only_claims_records_for_the_current_terminal_app(self):
        vscode = self.write_marker(ID_A, self.project, launcher="copilot", app="vscode", age=10)
        iterm = self.write_marker(ID_B, self.project, launcher="copilot", app="iTerm.app")
        self.open_and_close(self.project, TERM_PROGRAM="iTerm.app")
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_B}"]])
        self.assertTrue(vscode.exists())
        self.assertFalse(iterm.exists())
        self.open_and_close(self.project, TERM_PROGRAM="vscode")
        self.assertEqual(self.args()[-1], ["copilot", f"--resume={ID_A}"])
        self.assertFalse(vscode.exists())

    def test_new_launch_saves_terminal_app_and_keeps_it_after_hangup(self):
        pid, fd = self.interactive_shell(self.project, TERM_PROGRAM="vscode", AGENT_TEST_HANG="1")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        marker = next(self.sessions.glob("????????-????-????-????-????????????"))
        self.assertEqual(marker.read_text().splitlines()[7], "vscode")
        self.hang_up(pid, fd)
        self.assertEqual(marker.read_text().splitlines()[7], "vscode")
        self.open_and_close(self.project, TERM_PROGRAM="iTerm.app")
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(marker.exists())

    def test_legacy_record_without_app_can_still_restore(self):
        self.write_marker(ID_A, self.project, launcher="copilot")
        self.open_and_close(self.project, TERM_PROGRAM="iTerm.app")
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_cplt_new_session_gets_allow_all_and_id_and_clean_exit_removes_marker(self):
        self.run_zsh("cplt -d 'my dir'")
        [call] = self.args()
        self.assertEqual(call[:-1], ["cplt", "-d", "my dir", *CONTEXT, "--", "--allow-all", "--session-id"])
        self.assertRegex(call[-1], f"^{UUID}$")
        self.assertEqual(self.markers(), [])

    def test_copilot_new_session_gets_id_without_allow_all(self):
        self.run_zsh("copilot --model x")
        [call] = self.args()
        self.assertEqual(call[:2], ["copilot", "--session-id"])
        self.assertRegex(call[2], f"^{UUID}$")
        self.assertEqual(call[3:], ["--model", "x"])
        self.assertEqual(self.markers(), [])

    def test_agent_args_are_kept(self):
        self.run_zsh(f"cplt --preset strict -- --resume={ID_A} --model x; copilot --resume {ID_A}")
        self.assertEqual(self.args(), [
            ["cplt", "--preset", "strict", *CONTEXT, "--", "--allow-all", f"--resume={ID_A}", "--model", "x"],
            ["copilot", "--resume", ID_A],
        ])

    def test_untrackable_runs_get_no_session_id(self):
        self.run_zsh("cplt -- -p 'fix it'; copilot --resume --prompt='fix it'; copilot --acp")
        self.assertEqual(self.args(), [
            ["cplt", "--", "--allow-all", "-p", "fix it"],
            ["copilot", "--resume", "--prompt=fix it"],
            ["copilot", "--acp"],
        ])
        self.assertEqual(self.markers(), [])

    def test_copilot_picker_exit_without_selection_removes_marker(self):
        self.run_zsh("copilot --resume; copilot --continue")
        self.assertEqual(self.args(), [["copilot", "--resume"], ["copilot", "--continue"]])
        self.assertEqual(self.markers(), [])

    def test_subcommands_and_other_agents_pass_through(self):
        self.run_zsh("cplt config show; cplt exec -- env; cplt --agent opencode; cplt --agent=pi -- x; "
                     "cplt --preset strict config path; cplt --no-allow-tmp-exec doctor; "
                     "copilot login; copilot --version; copilot mcp list")
        self.assertEqual(self.args(), [
            ["cplt", "config", "show"], ["cplt", "exec", "--", "env"], ["cplt", "--agent", "opencode"],
            ["cplt", "--agent=pi", "--", "x"], ["cplt", "--preset", "strict", "config", "path"],
            ["cplt", "--no-allow-tmp-exec", "doctor"], ["copilot", "login"], ["copilot", "--version"],
            ["copilot", "mcp", "list"],
        ])
        self.assertEqual(self.markers(), [])

    def test_cplt_runs_other_agents_from_config_or_path_unchanged(self):
        self.run_zsh("cplt -- -x", AGENT_TEST_CPLT_AGENT="opencode")
        self.run_zsh("cplt --agent copilot -- -x", AGENT_TEST_CPLT_AGENT="opencode")
        (self.bin / "copilot").unlink()
        self.run_zsh("cplt -- -x")
        calls = self.args()
        self.assertEqual(calls[0], ["cplt", "--", "-x"])
        self.assertEqual(calls[1][:-2], ["cplt", "--agent", "copilot", *CONTEXT, "--", "--allow-all", "--session-id"])
        self.assertRegex(calls[1][-2], f"^{UUID}$")
        self.assertEqual(calls[1][-1], "-x")
        self.assertEqual(calls[2], ["cplt", "--", "-x"])

    def test_cplt_value_options_are_read_from_installed_help(self):
        self.run_zsh("cplt --allow-write-state '/tmp/tool state'; "
                     "cplt --future-option config --agent claude")
        calls = self.args()
        self.assertEqual(calls[0][:-1], ["cplt", "--allow-write-state", "/tmp/tool state",
                                       *CONTEXT, "--", "--allow-all", "--session-id"])
        self.assertIn("--session-id", calls[0])
        self.assertEqual(calls[1][:-1], ["cplt", "--future-option", "config", "--agent",
                                       "claude", *CONTEXT, "--", "--dangerously-skip-permissions", "--session-id"])
        self.assertIn("--dangerously-skip-permissions", calls[1])
        self.assertIn("--session-id", calls[1])
        self.assertEqual(self.markers(), [])

    def test_failed_session_returns_its_status_and_removes_marker(self):
        result = self.run_zsh("cplt; print cplt=$?; copilot; print copilot=$?", AGENT_TEST_STATUS="3")
        self.assertIn("cplt=3", result.stdout)
        self.assertIn("copilot=3", result.stdout)
        self.assertEqual(self.markers(), [])

    def test_no_wrappers_without_the_commands(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        result = self.run_zsh("print -r -- ${+functions[cplt]} ${+functions[copilot]} ${+functions[claude]}",
                              PATH=f"{empty}:/usr/bin:/bin")
        self.assertEqual(result.stdout.split(), ["0", "0", "0"])
        self.write_marker(ID_A, self.project)
        (self.bin / "cplt").unlink()
        (self.bin / "copilot").unlink()
        (self.bin / "claude").unlink()
        self.open_and_close(self.project)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.recorded(), [ID_A])

    def test_closed_terminal_leaves_session_that_next_terminal_resumes(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1")
        os.write(fd, b"cplt --repo-dir 'a b'\n")
        self.wait_for_calls(fd, 1)
        session_id = self.calls()[0][-1]
        # Wait for the follower, so the hang-up cannot race its first poll.
        records = [session_id, f"{session_id}.current"]
        self.drain(fd, lambda _: self.recorded() == records)
        self.hang_up(pid, fd)
        self.assertEqual(self.recorded(), records)

        self.open_and_close(self.other)
        self.assertEqual(len(self.calls()), 1)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1],
                         ["cplt", "--agent", "copilot", "--repo-dir", "a b", *CONTEXT, "--", "--allow-all",
                          f"--resume={session_id}"])
        self.assertEqual(self.calls()[1][1], str(self.project))
        self.assertEqual(self.markers(), [])

        self.open_and_close(self.project)
        self.assertEqual(len(self.calls()), 2)

    def test_session_resumed_without_writing_to_it_is_the_one_resumed(self):
        (self.tmp / "copilot/session-state" / ID_B).mkdir(parents=True)
        (self.tmp / "copilot/session-state" / ID_B / "events.jsonl").write_text("{}\n")
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=ID_B)
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        launched = self.calls()[0][-1]
        self.drain(fd, lambda _: self.current(launched) == ID_B)
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["copilot", f"--resume={ID_B}"])

    def test_session_switched_to_is_followed_through_locks_without_log(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=ID_B, AGENT_TEST_NO_LOG="1")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        launched = self.calls()[0][-1]
        self.drain(fd, lambda _: self.current(launched) == ID_B)
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["copilot", f"--resume={ID_B}"])
        self.assertEqual(self.markers(), [])

    def test_switching_back_to_an_earlier_session_is_followed(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=f"{ID_B},LAUNCHED")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        launched = self.calls()[0][-1]
        seen = set()

        def switched_back(_):
            current = self.current(launched)
            seen.add(current)
            return ID_B in seen and current == launched
        self.drain(fd, switched_back)
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["copilot", f"--resume={launched}"])

    def test_session_without_conversation_is_dropped(self):
        self.write_marker(ID_A, self.project, events=False)
        self.write_marker(ID_B, self.project, launcher="copilot", age=-60)
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_B}"]])
        self.assertEqual(self.markers(), [])

    def test_terminals_in_one_repository_each_resume_one_session_oldest_first(self):
        self.write_marker(ID_B, self.project, launcher="copilot", age=60)
        self.write_marker(ID_A, self.project, launcher="cplt", age=120)
        first = self.interactive_shell(self.project, AGENT_TEST_HANG="1")
        self.wait_for_calls(first[1], 1)
        second = self.interactive_shell(self.project, AGENT_TEST_HANG="1")
        self.wait_for_calls(second[1], 2)
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [
            ["cplt", "--agent", "copilot", *CONTEXT, "--", "--allow-all", f"--resume={ID_A}"],
            ["copilot", f"--resume={ID_B}"],
        ])
        records = [ID_A, f"{ID_A}.current", ID_B, f"{ID_B}.current"]
        self.drain(first[1], lambda _: self.recorded() == records)
        self.hang_up(*first)
        self.hang_up(*second)
        self.assertEqual(self.recorded(), records)
        # Records are named per launch, so a resumed session never recreates
        # the record name another restored terminal may be about to claim.
        self.assertFalse({ID_A, ID_B} & set(self.markers()))

    def test_session_running_in_another_terminal_is_not_resumed(self):
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())],
                                 capture_output=True, text=True, check=True).stdout.split()
        started = " ".join(started)
        self.write_marker(ID_A, self.project, pid=os.getpid(), started=started)
        self.open_and_close(self.project)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.recorded(), [ID_A])

    def test_session_whose_shell_pid_was_reused_is_resumed(self):
        self.write_marker(ID_A, self.project, launcher="copilot", pid=os.getpid(),
                          started="Thu Jan  1 00:00:00 2026")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_old_record_of_a_live_shell_is_not_deleted(self):
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())],
                                 capture_output=True, text=True, check=True).stdout.split()
        marker = self.write_marker(ID_A, self.project, age=8 * 86400,
                                   pid=os.getpid(), started=" ".join(started))
        self.open_and_close(self.project)
        self.assertTrue(marker.exists())
        self.assertEqual(self.calls(), [])

    def test_old_launch_with_recent_activity_is_resumed(self):
        marker = self.write_marker(ID_A, self.project, launcher="copilot", age=8 * 86400)
        marker.with_name(f"{marker.name}.current").write_text(f"{ID_A}\n")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])
        self.assertEqual(self.markers(), [])

    def test_expired_orphans_are_cleaned_but_live_claims_and_foreign_files_are_kept(self):
        for owner in (99999999, os.getpid()):
            with self.subTest(owner=owner):
                marker = self.write_marker(ID_A, self.project, age=8 * 86400)
                claim = marker.with_name(f"{marker.name}.{owner}")
                marker.rename(claim)
                current = marker.with_name(f"{marker.name}.current")
                current.write_text(f"{ID_A}\n")
                foreign = marker.with_name(f"{marker.name}.notes")
                foreign.write_text("not managed by the wrapper\n")
                stamp = time.time() - 8 * 86400
                os.utime(current, (stamp, stamp))
                os.utime(foreign, (stamp, stamp))
                self.open_and_close(self.project)
                self.assertEqual(claim.exists(), owner == os.getpid())
                self.assertEqual(current.exists(), owner == os.getpid())
                self.assertTrue(foreign.exists())
                self.assertEqual(self.calls(), [])

    def test_failed_automatic_resume_keeps_record_for_next_terminal(self):
        self.write_marker(ID_A, self.project, launcher="cplt", args="--preset strict")
        self.open_and_close(self.project, AGENT_TEST_FAIL_EARLY="1")
        self.assertIsNotNone(self.record(ID_A))
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [
            ["cplt", "--agent", "copilot", "--preset", "strict", *CONTEXT, "--", "--allow-all",
             f"--resume={ID_A}"],
        ] * 2)
        self.assertEqual(self.markers(), [])

    def test_closed_restored_session_with_nonzero_exit_has_only_one_record(self):
        self.write_marker(ID_A, self.project, launcher="copilot")
        pid, fd = self.interactive_shell(
            self.project, leader="trap '' HUP;", AGENT_TEST_HANG="1",
            AGENT_TEST_HANG_SECONDS="0.6", AGENT_TEST_HUP_STATUS="129", AGENT_TEST_STATUS="129",
        )
        self.wait_for_calls(fd, 1)
        self.drain(fd, lambda _: ID_A in self.currents())
        self.hang_up(pid, fd)
        self.assertEqual(self.recorded().count(ID_A), 1)
        self.open_and_close(self.project)
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]] * 2)

    def test_crash_after_successful_restore_does_not_restore_original_retry_record(self):
        self.write_marker(ID_A, self.project, launcher="copilot")
        self.open_and_close(self.project, AGENT_TEST_HANG="1",
                            AGENT_TEST_HANG_SECONDS="0.5", AGENT_TEST_STATUS="3")
        self.assertEqual(self.markers(), [])
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_reused_pid_log_does_not_replace_recorded_session(self):
        marker = self.write_marker(ID_A, self.project, launcher="copilot", age=180)
        marker.with_name(f"{marker.name}.current").write_text(f"{ID_A}\n")
        now = time.time()
        self.write_snapshot(marker, 41002, int(now) - 120, now - 60)
        logs = self.tmp / "copilot/logs"
        logs.mkdir()
        (logs / f"process-{int(now * 1000)}-41002.log").write_text(
            f"[INFO] Registering foreground session: {ID_B}\n")
        folder = self.tmp / "copilot/session-state" / ID_B
        folder.mkdir()
        (folder / "events.jsonl").write_text("{}\n")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_reused_pid_session_file_or_lock_does_not_replace_recorded_session(self):
        for agent in ("copilot", "claude"):
            with self.subTest(agent=agent):
                marker = self.write_marker(ID_A, self.project, launcher=agent, agent=agent, age=180)
                marker.with_name(f"{marker.name}.current").write_text(f"{ID_A}\n")
                now = time.time()
                self.write_snapshot(marker, 99999999, int(now) - 120, now - 60)
                if agent == "claude":
                    folder = self.tmp / "claude/sessions"
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "99999999.json").write_text(json.dumps({"sessionId": ID_B}))
                    (self.tmp / "claude/projects/-project" / f"{ID_B}.jsonl").touch()
                else:
                    folder = self.tmp / "copilot/session-state" / ID_B
                    folder.mkdir()
                    (folder / "events.jsonl").write_text("{}\n")
                    (folder / "inuse.99999999.lock").touch()
                self.open_and_close(self.project)
                expected = ([agent, "--resume", ID_A] if agent == "claude"
                            else [agent, f"--resume={ID_A}"])
                self.assertEqual(self.args()[-1], expected)
                self.assertEqual(self.markers(), [])

    def test_rotated_foreground_registration_is_used_when_current_log_has_none(self):
        marker = self.write_marker(ID_A, self.project, launcher="copilot", age=180)
        marker.with_name(f"{marker.name}.current").write_text(f"{ID_A}\n")
        now = time.time()
        started = int(now) - 120
        self.write_snapshot(marker, 41002, started, now)
        logs = self.tmp / "copilot/logs"
        logs.mkdir()
        prefix = f"process-{started * 1000 + 100}-41002"
        (logs / f"{prefix}.1.log").write_text(f"[INFO] Registering foreground session: {ID_B}\n")
        (logs / f"{prefix}.log").write_text("[INFO] Tool finished\n")
        folder = self.tmp / "copilot/session-state" / ID_B
        folder.mkdir()
        (folder / "events.jsonl").write_text("{}\n")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_B}"]])

    def test_log_from_an_older_pid_generation_is_rejected_even_if_modified_later(self):
        marker = self.write_marker(ID_B, self.project, launcher="copilot", age=180)
        now = time.time()
        started = int(now) - 30
        self.write_snapshot(marker, 41002, started, now)
        logs = self.tmp / "copilot/logs"
        logs.mkdir()
        (logs / f"process-{(started + 1) * 1000}-41002.log").write_text(
            f"[INFO] Registering foreground session: {ID_A}\n")
        old = logs / f"process-{(started - 30) * 1000}-41002.log"
        old.write_text(f"[INFO] Registering foreground session: {ID_B}\n")
        os.utime(old, (now + 1, now + 1))
        folder = self.tmp / "copilot/session-state" / ID_A
        folder.mkdir()
        (folder / "events.jsonl").write_text("{}\n")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_failed_restore_does_not_extend_original_activity_deadline(self):
        marker = self.write_marker(ID_A, self.project, launcher="copilot", age=6 * 86400 + 120)
        activity = time.time() - 6 * 86400
        current = marker.with_name(f"{marker.name}.current")
        current.write_text(f"{ID_A}\n")
        os.utime(current, (activity, activity))
        started = int(activity) - 60
        self.write_snapshot(marker, 41002, started, activity)
        logs = self.tmp / "copilot/logs"
        logs.mkdir()
        (logs / f"process-{(started + 1) * 1000}-41002.log").write_text(
            f"[INFO] Registering foreground session: {ID_A}\n")
        self.open_and_close(self.project, AGENT_TEST_FAIL_EARLY="1")
        self.assertIsNotNone(self.record(ID_A))
        self.assertAlmostEqual(current.stat().st_mtime, activity, delta=1)

    def test_missing_session_capture_backs_off_to_normal_poll_interval(self):
        marker = self.write_marker("", self.project, events=False)
        result = self.run_zsh(f"""
_agent_session_descendants() {{ return 0; }}
_agent_session_capture() {{ return 1; }}
calls=0
sleep() {{
  print -r -- "$1"
  (( ++calls == 12 )) && rm -f -- "{marker}"
}}
_agent_session_follow "{marker}" $$ copilot
""", AGENT_SESSION_FOLLOW_INTERVAL="")
        self.assertEqual(result.stdout.splitlines()[-1], "5")

    def test_dead_session_expires_after_seven_days_not_eight(self):
        self.write_marker(ID_A, self.project, age=7 * 86400 + 60)
        self.open_and_close(self.project)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.markers(), [])

    def test_dead_session_just_under_seven_days_is_still_restored(self):
        self.write_marker(ID_A, self.project, launcher="copilot", age=7 * 86400 - 60)
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])

    def test_session_switch_just_before_terminal_closes_is_captured(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1",
                                         AGENT_SESSION_FOLLOW_INTERVAL="2")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        launched = self.args()[0][2]
        self.drain(fd, lambda _: self.current(launched) == launched)
        log, = (self.tmp / "copilot/logs").glob("process-*.log")
        with log.open("a") as stream:
            stream.write(f"2026-01-01T00:00:00.000Z [INFO] Registering foreground session: {ID_B}\n")
        (self.tmp / "copilot/session-state" / ID_B).mkdir()
        (self.tmp / "copilot/session-state" / ID_B / "events.jsonl").write_text("{}\n")
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[-1], ["copilot", f"--resume={ID_B}"])

    def test_picker_selection_before_first_regular_poll_is_captured(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1",
                                         AGENT_TEST_SWITCH=ID_A,
                                         AGENT_SESSION_FOLLOW_INTERVAL="30")
        os.write(fd, b"copilot --resume\n")
        self.wait_for_calls(fd, 1)
        self.drain(fd, lambda _: (self.tmp / "copilot/session-state" / ID_A / "events.jsonl").exists())
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[-1], ["copilot", f"--resume={ID_A}"])

    def test_claude_new_session_gets_id_without_skipping_permissions(self):
        self.run_zsh("claude --model opus 'fix it'; claude -p 'fix it'; claude mcp list; claude --version")
        calls = self.args()
        self.assertEqual(calls[0][:2], ["claude", "--session-id"])
        self.assertRegex(calls[0][2], f"^{UUID}$")
        self.assertEqual(calls[0][3:], ["--model", "opus", "fix it"])
        self.assertEqual(calls[1:], [["claude", "-p", "fix it"], ["claude", "mcp", "list"], ["claude", "--version"]])
        self.assertEqual(self.markers(), [])

    def test_cplt_starts_claude_without_permission_prompts(self):
        self.run_zsh(f"cplt --agent claude -- --resume {ID_A}")
        self.run_zsh("cplt", AGENT_TEST_CPLT_AGENT="claude")
        calls = self.args()
        self.assertEqual(calls[0], ["cplt", "--agent", "claude", *CONTEXT, "--", "--dangerously-skip-permissions",
                                    "--resume", ID_A])
        self.assertEqual(calls[1][:-1], ["cplt", *CONTEXT, "--", "--dangerously-skip-permissions", "--session-id"])
        self.assertNotIn("--allow-all", calls[0] + calls[1])

    def test_closed_claude_terminal_resumes_the_session_switched_to(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=ID_B)
        os.write(fd, b"cplt --agent claude\n")
        self.wait_for_calls(fd, 1)
        launched = self.calls()[0][-1]
        self.drain(fd, lambda _: self.current(launched) == ID_B)
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["cplt", "--agent", "claude", *CONTEXT, "--", "--dangerously-skip-permissions",
                                          "--resume", ID_B])
        self.assertEqual(self.markers(), [])

    def test_claude_continue_is_resumed_once_followed(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=ID_A)
        os.write(fd, b"claude --continue\n")
        self.wait_for_calls(fd, 1)
        self.assertEqual(self.args()[0], ["claude", "--continue"])
        self.drain(fd, lambda _: ID_A in self.currents())
        self.hang_up(pid, fd)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["claude", "--resume", ID_A])

    def test_copilot_selected_session_is_followed_and_restored(self):
        for command in ("copilot --resume", "copilot -r", "copilot --resume my-name",
                        "copilot --resume=my-name", "copilot --resume=0f6a2b4",
                        "copilot --resume --model x", "copilot --continue",
                        "cplt -- --resume", "cplt -- --continue",
                        "cplt --resume", "cplt --continue", f"cplt --resume={ID_B}"):
            with self.subTest(command=command):
                count = len(self.calls())
                pid, fd = self.interactive_shell(
                    self.project, AGENT_TEST_HANG="1", AGENT_TEST_SWITCH=ID_A,
                )
                os.write(fd, f"{command}\n".encode())
                self.wait_for_calls(fd, count + 1)
                self.assertNotIn("--session-id", self.args()[-1])
                self.drain(fd, lambda _: ID_A in self.currents())
                self.hang_up(pid, fd)

                pid, fd = self.interactive_shell(self.project)
                self.wait_for_calls(fd, count + 2)
                self.exit_shell(pid, fd)
                expected = (["cplt", "--agent", "copilot", *CONTEXT, "--", "--allow-all"]
                            if command.startswith("cplt") else ["copilot"])
                self.assertEqual(self.args()[-1], expected + [f"--resume={ID_A}"])
                self.assertEqual(self.markers(), [])

    def test_claude_session_without_transcript_is_dropped(self):
        self.write_marker(ID_A, self.project, launcher="claude", agent="claude", events=False)
        self.write_marker(ID_B, self.project, launcher="claude", agent="claude", age=-60)
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["claude", "--resume", ID_B]])
        self.assertEqual(self.markers(), [])

    def test_ctrl_c_ends_the_session_without_leaving_a_record(self):
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        session_id = self.args()[0][2]
        self.assertIsNotNone(self.record(session_id))
        os.write(fd, b"\x03")
        self.drain(fd, lambda _: self.markers() == [])
        self.exit_shell(pid, fd)
        self.assertEqual(self.markers(), [])

    def test_closed_terminal_keeps_session_when_shell_is_not_session_leader(self):
        # The terminal hangs up the agent but not zsh, so zsh sees the agent exit normally.
        # A leader that exits revokes the terminal; one that stays leaves it hung up.
        for leader in (":;", "trap '' HUP;"):
            with self.subTest(leader=leader):
                self.check_closed_terminal_under_leader(leader)
                shutil.rmtree(self.sessions)
                self.log.unlink()

    def check_closed_terminal_under_leader(self, leader):
        pid, fd = self.interactive_shell(self.project, leader=leader, AGENT_TEST_HANG="1")
        os.write(fd, b"copilot\n")
        self.wait_for_calls(fd, 1)
        session_id = self.args()[0][2]
        shell = int(subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()[0])
        # A leader that ignores HUP passes that on, so the agent finishes its 30-second hang.
        self.hang_up(pid, fd, timeout=45)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                os.kill(shell, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        self.assertIsNotNone(self.record(session_id))

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1], ["copilot", f"--resume={session_id}"])

    def test_cplt_session_resumes_with_its_recorded_agent(self):
        self.write_marker(ID_A, self.project, launcher="cplt", args="--preset strict")
        self.open_and_close(self.project, AGENT_TEST_CPLT_AGENT="claude")
        self.assertEqual(self.args(), [
            ["cplt", "--agent", "copilot", "--preset", "strict", *CONTEXT, "--", "--allow-all", f"--resume={ID_A}"],
        ])

    def test_wrappers_work_under_strict_user_options(self):
        self.run_zsh("setopt nounset ksh_arrays; copilot; cplt --agent claude -- --resume " + ID_A)
        calls = self.args()
        self.assertEqual(calls[0][:2], ["copilot", "--session-id"])
        self.assertEqual(calls[1], ["cplt", "--agent", "claude", *CONTEXT, "--", "--dangerously-skip-permissions",
                                    "--resume", ID_A])

    def test_vscode_resumes_only_in_a_terminal_it_displays(self):
        self.write_marker(ID_A, self.project)
        pid, fd = self.interactive_shell(self.project, size=(30, 80), TERM_PROGRAM="vscode")
        deadline = time.time() + 3
        self.drain(fd, lambda _: time.time() > deadline)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.recorded(), [ID_A])
        self.exit_shell(pid, fd)

        pid, fd = self.interactive_shell(self.project, size=(30, 80), TERM_PROGRAM="vscode")
        deadline = time.time() + 0.5
        self.drain(fd, lambda _: time.time() > deadline)
        set_size(fd, 43, 106)
        self.wait_for_calls(fd, 1)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[0][-1], f"--resume={ID_A}")

    def test_vscode_resumes_when_terminal_is_displayed_later(self):
        self.write_marker(ID_A, self.project)
        pid, fd = self.interactive_shell(self.project, size=(30, 80), TERM_PROGRAM="vscode")
        self.drain(fd, lambda output: b"\r% " in output or b"\r# " in output)
        self.assertEqual(self.calls(), [])
        set_size(fd, 43, 106)
        self.wait_for_calls(fd, 1)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[-1][-1], f"--resume={ID_A}")

    def test_vscode_does_not_resume_after_user_enters_a_command(self):
        self.write_marker(ID_A, self.project)
        pid, fd = self.interactive_shell(self.project, size=(30, 80), TERM_PROGRAM="vscode")
        os.write(fd, b"print USER_COMMAND_RAN\n")
        self.drain(fd, lambda output: b"\r\nUSER_COMMAND_RAN\r\n" in output)
        set_size(fd, 43, 106)
        self.exit_shell(pid, fd)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.recorded(), [ID_A])

    def test_deferred_resume_agent_can_read_the_terminal(self):
        self.write_marker(ID_A, self.project)
        received = self.tmp / "agent-input"
        pid, fd = self.interactive_shell(self.project, size=(30, 80), TERM_PROGRAM="vscode",
                                         AGENT_TEST_INPUT=str(received))
        self.drain(fd, lambda output: b"\r% " in output or b"\r# " in output)
        set_size(fd, 43, 106)
        self.drain(fd, lambda output: b"AGENT_INPUT_READY" in output)
        os.write(fd, b"answer\n")
        self.drain(fd, lambda _: received.exists() and received.read_text() == "answer")
        self.exit_shell(pid, fd)
        self.assertEqual(self.markers(), [])

    def test_ctrl_c_during_automatic_resume_does_not_leave_retry_record(self):
        self.write_marker(ID_A, self.project, launcher="copilot")
        pid, fd = self.interactive_shell(self.project, AGENT_TEST_HANG="1")
        self.wait_for_calls(fd, 1)
        os.write(fd, b"\x03")
        self.drain(fd, lambda _: self.markers() == [])
        self.exit_shell(pid, fd)
        self.assertEqual(self.markers(), [])

    def test_hook_observation_can_restore_without_a_follower_poll(self):
        marker = self.write_marker("", self.project, launcher="copilot", events=False)
        folder = self.tmp / "copilot/session-state" / ID_A
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text("{}\n")
        observed = self.tmp / ".cache/agent-sessions"
        observed.mkdir(parents=True)
        (observed / marker.name).write_text(f"{ID_A}\n")
        self.open_and_close(self.project)
        self.assertEqual(self.args(), [["copilot", f"--resume={ID_A}"]])
        self.assertEqual(list(observed.iterdir()), [])

    def test_latest_observation_wins_when_dead_claude_process_cannot_be_read(self):
        for latest in ("current", "hook"):
            with self.subTest(latest=latest):
                calls_before = len(self.calls())
                marker = self.write_marker(ID_A, self.project, launcher="claude", agent="claude")
                (self.tmp / "claude/projects/-project" / f"{ID_B}.jsonl").touch()
                current = marker.with_name(f"{marker.name}.current")
                observed = self.tmp / ".cache/agent-sessions"
                observed.mkdir(parents=True, exist_ok=True)
                hook = observed / marker.name
                current.write_text(f"{ID_B if latest == 'current' else ID_A}\n")
                hook.write_text(f"{ID_B if latest == 'hook' else ID_A}\n")
                older = hook if latest == "current" else current
                stamp = time.time() - 60
                os.utime(older, (stamp, stamp))
                self.write_snapshot(marker, 99999999, int(stamp) - 60, time.time())
                self.open_and_close(self.project)
                self.assertEqual(len(self.calls()), calls_before + 1)
                self.assertEqual(self.args()[-1], ["claude", "--resume", ID_B])

    def test_resume_is_skipped_when_disabled_or_stale(self):
        marker = self.write_marker(ID_A, self.project)
        self.open_and_close(self.project, CPLT_NO_RESUME="1")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.recorded(), [ID_A])

        stale = time.time() - 8 * 86400
        os.utime(marker, (stale, stale))
        self.open_and_close(self.project)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.markers(), [])


if __name__ == "__main__":
    unittest.main()
