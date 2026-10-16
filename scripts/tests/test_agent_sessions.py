"""Tests for the copilot/cplt wrappers and session resuming in zsh/conf.d/agent-sessions.zsh."""

import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import uuid


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
signal.signal(signal.SIGHUP, lambda *_: (time.sleep(0.3), sys.exit(0)))
# AGENT_TEST_CPLT_AGENT is the sandbox.agent value in cplt's config.
if Path(sys.argv[0]).name == "cplt" and args[:3] == ["config", "get", "sandbox.agent"]:
    print(os.environ.get("AGENT_TEST_CPLT_AGENT", ""))
    sys.exit(0)
with open(os.environ["AGENT_TEST_LOG"], "a") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, os.getcwd()] + args) + "\\n")
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
for i, target in enumerate(([session] if session else []) + switches):
    if i:
        time.sleep(1.1)
    target = session if target == "LAUNCHED" else target
    # AGENT_TEST_NO_LOG acts like Copilot versions that do not log this line.
    if not os.environ.get("AGENT_TEST_NO_LOG"):
        with open(home / "logs" / f"process-1-{os.getpid()}.log", "a") as log:
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
    time.sleep(30)
sys.exit(int(os.environ.get("AGENT_TEST_STATUS", "0")))
"""
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ID_A = "0f6a2b4c-1d3e-4f50-8a9b-0c1d2e3f4a5b"
ID_B = "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"


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

    def interactive_shell(self, cwd, leader=None, **env):
        """With leader, zsh runs under that sh command, as iTerm2 runs it under login."""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            if leader:
                os.execve("/bin/sh", ["sh", "-c", f"{leader} /bin/zsh -d -i; :"], {**self.env, **env})
            os.execve("/bin/zsh", ["zsh", "-d", "-i"], {**self.env, **env})
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

    def hang_up(self, pid, fd):
        os.close(fd)
        os.waitpid(pid, 0)

    def write_marker(self, session_id, cwd, launcher="cplt", args="", age=0, pid=99999999, started="",
                     events=True, agent="copilot"):
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
        stamp = time.time() - age
        os.utime(marker, (stamp, stamp))
        return marker

    def test_cplt_new_session_gets_allow_all_and_id_and_clean_exit_removes_marker(self):
        self.run_zsh("cplt -d 'my dir'")
        [call] = self.args()
        self.assertEqual(call[:6], ["cplt", "-d", "my dir", "--", "--allow-all", "--session-id"])
        self.assertRegex(call[6], f"^{UUID}$")
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
            ["cplt", "--preset", "strict", "--", "--allow-all", f"--resume={ID_A}", "--model", "x"],
            ["copilot", "--resume", ID_A],
        ])

    def test_untrackable_runs_get_no_session_id(self):
        self.run_zsh("cplt -- -p 'fix it'; cplt -- --continue; copilot --resume; copilot --resume my-name; copilot --acp")
        self.assertEqual(self.args(), [
            ["cplt", "--", "--allow-all", "-p", "fix it"],
            ["cplt", "--", "--allow-all", "--continue"],
            ["copilot", "--resume"],
            ["copilot", "--resume", "my-name"],
            ["copilot", "--acp"],
        ])

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
        self.assertEqual(calls[1][:6], ["cplt", "--agent", "copilot", "--", "--allow-all", "--session-id"])
        self.assertEqual(calls[2], ["cplt", "--", "-x"])

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
        self.hang_up(pid, fd)
        self.assertEqual(self.recorded(), [session_id])

        self.open_and_close(self.other)
        self.assertEqual(len(self.calls()), 1)

        pid, fd = self.interactive_shell(self.project)
        self.wait_for_calls(fd, 2)
        self.exit_shell(pid, fd)
        self.assertEqual(self.args()[1],
                         ["cplt", "--agent", "copilot", "--repo-dir", "a b", "--", "--allow-all",
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
            ["cplt", "--agent", "copilot", "--", "--allow-all", f"--resume={ID_A}"],
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
        self.assertEqual(calls[0], ["cplt", "--agent", "claude", "--", "--dangerously-skip-permissions",
                                    "--resume", ID_A])
        self.assertEqual(calls[1][:4], ["cplt", "--", "--dangerously-skip-permissions", "--session-id"])
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
        self.assertEqual(self.args()[1], ["cplt", "--agent", "claude", "--", "--dangerously-skip-permissions",
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
        self.hang_up(pid, fd)
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
            ["cplt", "--agent", "copilot", "--preset", "strict", "--", "--allow-all", f"--resume={ID_A}"],
        ])

    def test_wrappers_work_under_strict_user_options(self):
        self.run_zsh("setopt nounset ksh_arrays; copilot; cplt --agent claude -- --resume " + ID_A)
        calls = self.args()
        self.assertEqual(calls[0][:2], ["copilot", "--session-id"])
        self.assertEqual(calls[1], ["cplt", "--agent", "claude", "--", "--dangerously-skip-permissions",
                                    "--resume", ID_A])

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
