"""Offline regression tests for agent-awake's hooks, hook files and root helper."""

from contextlib import redirect_stdout
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("agent_awake_cli", REPO / "scripts/agent-awake.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
DAEMON = REPO / "scripts/agent-awake-daemon.sh"

PMSET_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB/calls"
case "$*" in
    "-g") cat "$STUB/settings"; [ -x "$STUB/during-settings" ] && "$STUB/during-settings" ;;
    "-g batt") cat "$STUB/batt" ;;
    "-g sched") cat "$STUB/sched" ;;
    "-a disablesleep 1") printf ' SleepDisabled\\t\\t1\\n' > "$STUB/settings" ;;
    "-a disablesleep 0") printf ' SleepDisabled\\t\\t0\\n' > "$STUB/settings" ;;
esac
"""
STUBS = {
    "pmset": PMSET_STUB,
    "ioreg": '#!/bin/bash\ncat "$STUB/ioreg"\n',
    "id": "#!/bin/bash\necho 0\n",
    # Runs what a test wants to happen while the helper waits for the clamshell state.
    "sleep": '#!/bin/bash\n[ -x "$STUB/during-sleep" ] && "$STUB/during-sleep"\nexit 0\n',
}
LID_CLOSED = '"AppleClamshellState" = Yes\n"AppleClamshellCausesSleep" = Yes\n'
LID_CLOSED_WITH_DISPLAY = '"AppleClamshellState" = Yes\n"AppleClamshellCausesSleep" = No\n'
ON_BATTERY = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t{}%; discharging; 3:00 remaining\n"
ON_ADAPTER = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charged; 0:00 remaining\n"


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.holds = self.home / ".cache/agent-awake/holds"


class HookTests(HomeTestCase):
    def hook(self, action, agent, payload, pid=4242):
        with patch.object(cli, "agent_process", return_value=pid), redirect_stdout(io.StringIO()) as printed:
            with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                self.assertEqual(cli.main(["--hook", action, agent]), 0)
        self.assertEqual(printed.getvalue(), "")

    def test_a_turn_holds_from_prompt_to_stop(self):
        self.hook("start", "claude", {"session_id": "abc"})
        hold = self.holds / "claude-abc"
        self.assertEqual(hold.read_text(), "4242\n")
        os.utime(hold, (0, 0))
        self.hook("refresh", "claude", {"session_id": "abc"})
        self.assertGreater(hold.stat().st_mtime, time.time() - 60)
        self.hook("stop", "claude", {"session_id": "abc"})
        self.assertEqual(hold.read_text(), "released\n")

    def test_wrapped_session_is_observed_independently_of_its_awake_hold(self):
        marker = "0f6a2b4c-1d3e-4f50-8a9b-0c1d2e3f4a5b"
        session = "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"
        with patch.dict(os.environ, {"DOTFILES_AGENT_SESSION": marker, "DOTFILES_AGENT_KIND": "copilot"}):
            self.hook("start", "copilot", {"sessionId": session})
            observed = self.home / ".cache/agent-sessions" / marker
            self.assertEqual(observed.read_text(), f"{session}\n")
            self.hook("stop", "copilot", {"sessionId": session})
            self.assertEqual(observed.read_text(), f"{session}\n")
            self.assertEqual((self.holds / f"copilot-{session}").read_text(), "released\n")

    def test_subagent_events_and_other_agents_do_not_replace_session_observation(self):
        marker = "0f6a2b4c-1d3e-4f50-8a9b-0c1d2e3f4a5b"
        session = "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"
        with patch.dict(os.environ, {"DOTFILES_AGENT_SESSION": marker, "DOTFILES_AGENT_KIND": "copilot"}):
            self.hook("substart", "copilot", {"sessionId": session, "agentId": "kid"})
            self.hook("busy", "copilot", {"sessionId": session})
            self.hook("start", "claude", {"session_id": session})
        self.assertFalse((self.home / ".cache/agent-sessions").exists())

    def test_invalid_marker_cannot_write_a_session_observation(self):
        with patch.dict(os.environ, {"DOTFILES_AGENT_SESSION": "../outside", "DOTFILES_AGENT_KIND": "copilot"}):
            self.hook("start", "copilot", {"sessionId": "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"})
        self.assertFalse((self.home / ".cache/agent-sessions").exists())

    def test_prompt_without_a_wrapper_tag_does_not_register_for_restoration(self):
        with patch.dict(os.environ, {"DOTFILES_AGENT_SESSION": "", "DOTFILES_AGENT_KIND": ""}):
            self.hook("start", "copilot", {"sessionId": "1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c"})
        self.assertFalse((self.home / ".cache/agent-sessions").exists())
        self.assertTrue((self.holds / "copilot-1a2b3c4d-5e6f-4a70-8b9c-0d1e2f3a4b5c").is_file())

    def test_a_running_tool_call_marks_the_hold_busy_until_it_ends(self):
        self.hook("start", "codex", {"session_id": "t"})
        self.hook("busy", "codex", {"session_id": "t"}, pid=99)
        hold = self.holds / "codex-t"
        # The process found at the prompt is kept.
        self.assertEqual(hold.read_text(), "4242\nbusy\n")
        self.hook("refresh", "codex", {"session_id": "t"}, pid=99)
        self.assertEqual(hold.read_text(), "4242\n")

    def test_a_new_prompt_finds_the_agent_process_again(self):
        # A resumed session runs in a new process; keeping the old ID would make the helper ignore the hold.
        self.hook("start", "claude", {"session_id": "r"})
        self.hook("start", "claude", {"session_id": "r"}, pid=5151)
        self.assertEqual((self.holds / "claude-r").read_text(), "5151\n")

    def subagent_states(self, session):
        return [path.read_text() for path in sorted(self.holds.glob(f"{session}.sub.*"))]

    def test_a_copilot_turn_that_fails_releases_its_hold_but_retries_do_not(self):
        self.hook("start", "copilot", {"sessionId": "s"})
        self.hook("error", "copilot", {"sessionId": "s", "errorContext": "model_call", "recoverable": True,
                                       "error": {"message": "timeout"}})
        self.assertEqual((self.holds / "copilot-s").read_text(), "4242\n")
        self.hook("error", "copilot", {"sessionId": "s", "errorContext": "model_call", "recoverable": False,
                                       "error": {"message": "Failed to get response from the AI model"}})
        self.assertEqual((self.holds / "copilot-s").read_text(), "released\n")

    def test_copilot_subagents_are_held_by_id_and_outlive_their_parents_turn(self):
        # Shapes from a real Copilot session: subagentStart and subagentStop carry the parent's sessionId and
        # the subagent's agentId, and the subagent's own tool hooks use its agentId as their sessionId.
        self.hook("start", "copilot", {"sessionId": "parent"})
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid1", "agentName": "task"})
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid2", "agentName": "explore"})
        self.hook("stop", "copilot", {"sessionId": "parent"})
        sub = self.holds / "copilot-parent.sub.kid2"
        os.utime(sub, (0, 0))
        self.hook("busy", "copilot", {"sessionId": "kid2"})
        self.assertGreater(sub.stat().st_mtime, time.time() - 60)
        self.assertEqual((self.holds / "copilot-kid2").read_text(), "4242\nbusy\n")

        self.hook("substop", "copilot", {"sessionId": "parent", "agentId": "kid2"})
        self.assertEqual(sub.read_text(), "released\n")
        self.assertEqual((self.holds / "copilot-kid2").read_text(), "released\nended\n")
        self.assertEqual((self.holds / "copilot-parent.sub.kid1").read_text(), "4242\n")

    def test_a_copilot_subagent_that_dies_is_not_kept_held_by_its_parent(self):
        self.hook("start", "copilot", {"sessionId": "parent"})
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.hook("busy", "copilot", {"sessionId": "kid"})
        sub = self.holds / "copilot-parent.sub.kid"
        old = time.time() - 300
        os.utime(sub, (old, old))
        self.hook("busy", "copilot", {"sessionId": "parent"})
        self.assertEqual(sub.stat().st_mtime, old)

        self.hook("error", "copilot", {"sessionId": "kid", "recoverable": False})
        self.assertEqual(sub.read_text(), "released\n")
        self.assertEqual((self.holds / "copilot-parent").read_text(), "4242\nbusy\n")

    def test_a_stop_for_an_unknown_subagent_leaves_the_others_held(self):
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "live"})
        self.hook("substop", "copilot", {"sessionId": "parent", "agentId": "missing"})
        self.assertEqual((self.holds / "copilot-parent.sub.live").read_text(), "4242\n")

    def test_ending_a_session_ends_its_subagents_own_holds(self):
        self.hook("start", "copilot", {"sessionId": "parent"})
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.hook("busy", "copilot", {"sessionId": "kid"})
        self.hook("end", "copilot", {"sessionId": "parent"})
        for name in ("copilot-parent", "copilot-parent.sub.kid", "copilot-kid"):
            self.assertIn("released", (self.holds / name).read_text(), name)

    def test_late_tool_hooks_do_not_hold_an_ended_session_until_a_new_prompt(self):
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.hook("substop", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.hook("busy", "copilot", {"sessionId": "kid"})
        self.assertIn("released", (self.holds / "copilot-kid").read_text())
        self.assertLess((self.holds / "copilot-kid").stat().st_mtime, time.time() - cli.RELEASE_GRACE_SECONDS + 5)

        self.hook("start", "copilot", {"sessionId": "parent"})
        self.hook("end", "copilot", {"sessionId": "parent"})
        self.hook("refresh", "copilot", {"sessionId": "parent"})
        self.assertIn("released", (self.holds / "copilot-parent").read_text())
        self.hook("start", "copilot", {"sessionId": "parent"})
        self.assertEqual((self.holds / "copilot-parent").read_text(), "4242\n")

    def test_a_second_release_does_not_restart_the_grace_period(self):
        self.hook("substart", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.hook("busy", "copilot", {"sessionId": "kid"})
        self.hook("stop", "copilot", {"sessionId": "kid"})
        sub = self.holds / "copilot-parent.sub.kid"
        self.assertEqual(sub.read_text(), "released\n")
        old = time.time() - 300
        os.utime(sub, (old, old))
        self.hook("substop", "copilot", {"sessionId": "parent", "agentId": "kid"})
        self.assertEqual(sub.stat().st_mtime, old)

    def test_subagents_without_an_id_are_counted(self):
        self.hook("start", "codex", {"session_id": "s"})
        self.hook("substart", "codex", {"session_id": "s"})
        self.hook("substart", "codex", {"session_id": "s"})
        self.hook("substop", "codex", {"session_id": "s"})
        self.assertEqual(self.subagent_states("codex-s"), ["released\n", "4242\n"])

    def test_subagent_tool_calls_refresh_the_session_and_its_subagents(self):
        self.hook("start", "claude", {"session_id": "s"})
        self.hook("substart", "claude", {"session_id": "s", "agent_id": "a1"})
        sub = self.holds / "claude-s.sub.a1"
        os.utime(sub, (0, 0))
        self.hook("busy", "claude", {"session_id": "s", "agent_id": "a1"})
        self.assertEqual(sorted(path.name for path in self.holds.iterdir()), ["claude-s", sub.name])
        self.assertEqual((self.holds / "claude-s").read_text(), "4242\nbusy\n")
        self.assertGreater(sub.stat().st_mtime, time.time() - 60)

    def test_ending_the_session_releases_its_subagents(self):
        self.hook("start", "codex", {"session_id": "e"})
        self.hook("substart", "codex", {"session_id": "e"})
        self.hook("end", "codex", {"session_id": "e"})
        self.assertEqual((self.holds / "codex-e").read_text(), "released\nended\n")
        self.assertEqual(self.subagent_states("codex-e"), ["released\n"])

    def test_refresh_after_a_release_holds_again(self):
        # A queued message may continue a turn without a new prompt event; its first tool call holds again.
        self.hook("start", "codex", {"session_id": "t"})
        self.hook("stop", "codex", {"session_id": "t"})
        self.hook("refresh", "codex", {"session_id": "t"}, pid=77)
        self.assertEqual((self.holds / "codex-t").read_text(), "77\n")

    def test_stop_without_a_hold_and_bad_input_change_nothing(self):
        self.hook("stop", "codex", {"session_id": "none"})
        with patch("sys.stdin", io.StringIO("not json")), redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(cli.main(["--hook", "start", "nobody"]), 0)
        self.assertEqual(printed.getvalue(), "")
        self.assertEqual(list(self.holds.iterdir()), [])

    def test_day_old_holds_are_cleaned_up(self):
        self.holds.mkdir(parents=True)
        old = self.holds / "claude-crashed"
        old.write_text("1\n")
        os.utime(old, (time.time() - 25 * 3600,) * 2)
        self.hook("start", "claude", {"session_id": "new"})
        self.assertEqual(sorted(path.name for path in self.holds.iterdir()), ["claude-new"])


class StatusTests(HomeTestCase):
    def test_holds_keeping_the_mac_awake_come_first_then_by_latest_activity(self):
        self.holds.mkdir(parents=True)
        for name, content, age in (("a-old-finished", "released\n", 420), ("b-working", f"{os.getpid()}\n", 60),
                                   ("c-new-finished", "released\n", 120), ("d-just-finished", "released\n", 10),
                                   ("e-busy", f"{os.getpid()}\nbusy\n", 5)):
            (self.holds / name).write_text(content)
            os.utime(self.holds / name, (time.time() - age,) * 2)
        with patch.object(cli, "command_output", return_value=""), \
                patch.object(cli.agent_awake, "DAEMON_PLIST", self.home / "absent.plist"), \
                patch.object(cli.agent_awake, "LOG", self.home / "absent.log"), \
                redirect_stdout(io.StringIO()) as printed:
            cli.status()
        holds = printed.getvalue().split("Holds", 1)[1]
        order = [line.split()[1] for line in holds.splitlines() if line.startswith("  ")]
        self.assertEqual(order, ["e-busy", "d-just-finished", "b-working", "c-new-finished", "a-old-finished"])

    def print_holds(self):
        cli.session_name.cache_clear()
        with patch.object(cli, "command_output", return_value=""), \
                patch.object(cli.agent_awake, "DAEMON_PLIST", self.home / "absent.plist"), \
                patch.object(cli.agent_awake, "LOG", self.home / "absent.log"), \
                redirect_stdout(io.StringIO()) as printed:
            cli.status()
        return [line for line in printed.getvalue().split("Holds", 1)[1].splitlines() if line.startswith("  ")]

    def add_holds(self, *holds):
        self.holds.mkdir(parents=True)
        for name, content, age in holds:
            (self.holds / name).write_text(content)
            os.utime(self.holds / name, (time.time() - age,) * 2)

    def test_only_the_latest_finished_holds_are_listed(self):
        self.add_holds(*[(f"copilot-done{age}", "released\n", age * 60) for age in range(1, 9)],
                       ("copilot-working", f"{os.getpid()}\n", 900))
        lines = self.print_holds()
        self.assertEqual([line.split()[1] for line in lines[:6]],
                         ["copilot-working", "copilot-done1", "copilot-done2", "copilot-done3", "copilot-done4",
                          "copilot-done5"])
        self.assertEqual(lines[6].strip(), "3 older finished or expired holds not shown")
        self.assertEqual(len(lines), 7)

    def test_a_subagent_is_one_row_naming_its_parent_session(self):
        self.add_holds(("copilot-parent1234", "released\n", 300),
                       ("copilot-parent1234.sub.kid", f"{os.getpid()}\n", 200),
                       ("copilot-kid", f"{os.getpid()}\nbusy\n", 5),
                       ("codex-s.sub.1793021581507858000-61613", f"{os.getpid()}\n", 30))
        lines = self.print_holds()
        self.assertEqual(len(lines), 3)
        self.assertIn("copilot-kid", lines[0])
        self.assertIn("Copilot sub-agent of parent12, running a tool call, started 5 s ago", lines[0])
        self.assertIn("codex-s sub", lines[1])
        self.assertIn("Codex sub-agent of s, working", lines[1])
        self.assertIn("copilot-parent1234", lines[2])
        self.assertNotIn("sub-agent", lines[2])

    def test_rows_show_the_names_the_agents_give_their_sessions(self):
        # File shapes as each agent writes them on this Mac.
        copilot = self.home / ".copilot/session-state/p1"
        copilot.mkdir(parents=True)
        (copilot / "workspace.yaml").write_text("id: p1\nname: Configure Enonic Sandbox Commands\nuser_named: true\n")
        started = {"type": "subagent.started", "agentId": "kid",
                   "data": {"agentName": "task", "agentDisplayName": "prod-xp8-import"}}
        (copilot / "events.jsonl").write_text(json.dumps(started) + "\n" + "{}\n" * 3)
        codex = self.home / ".codex/session_index.jsonl"
        codex.parent.mkdir()
        codex.write_text("".join(json.dumps({"id": "c1", "thread_name": name}) + "\n"
                                 for name in ("Old name", "Wait for two minutes")))
        claude = self.home / ".claude/projects/-Users-me-app/a1.jsonl"
        claude.parent.mkdir(parents=True)
        claude.write_text("".join(json.dumps(entry) + "\n" for entry in (
            {"type": "custom-title", "customTitle": "Release prep", "sessionId": "a1"},
            {"type": "ai-title", "aiTitle": "Continue openclaw tasks", "sessionId": "a1"})))
        self.add_holds(("copilot-p1", f"{os.getpid()}\n", 1), ("copilot-p1.sub.kid", f"{os.getpid()}\n", 2),
                       ("codex-c1", f"{os.getpid()}\n", 3), ("claude-a1", f"{os.getpid()}\n", 4),
                       ("claude-gone", f"{os.getpid()}\n", 5))
        lines = [" ".join(line.split()[1:]) for line in self.print_holds()]
        self.assertEqual(lines, [
            "Configure Enonic Sand\u2026 Copilot, working, last activity 1 s ago",
            "prod-xp8-import Copilot sub-agent of \u201cConfigure Enonic Sandbox Comm\u2026\u201d, "
            "working, last activity 2 s ago",
            "Wait for two minutes Codex, working, last activity 3 s ago",
            "Release prep Claude, working, last activity 4 s ago",
            "claude-gone Claude, working, last activity 5 s ago",
        ])


class HookFileTests(HomeTestCase):
    def test_install_and_uninstall_keep_other_hooks(self):
        settings = self.home / ".claude/settings.json"
        settings.parent.mkdir()
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}
        settings.write_text(json.dumps({"model": "opus", "hooks": {"PostToolUse": [other]}}))
        for _ in range(2):
            cli.add_hooks("claude")
        data = json.loads(settings.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["PostToolUse"][0], other)
        self.assertEqual(len(data["hooks"]["PostToolUse"]), 2)
        self.assertEqual(data["hooks"]["Notification"][0]["matcher"],
                         "permission_prompt|idle_prompt|elicitation_dialog")
        self.assertEqual(cli.hooks_state("claude"), "installed")

        self.assertTrue(cli.remove_hooks("claude"))
        self.assertEqual(json.loads(settings.read_text()), {"model": "opus", "hooks": {"PostToolUse": [other]}})
        self.assertEqual(cli.hooks_state("claude"), "missing")

    def test_our_handler_is_removed_from_a_group_it_shares_with_another(self):
        hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": cli.hook_command("busy", "codex")},
            {"type": "command", "command": "lint"},
        ]}]}
        self.assertEqual(cli.without_ours(hooks), {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}]})

    def test_codex_file_is_removed_when_only_our_hooks_were_in_it(self):
        (self.home / ".codex").mkdir()
        cli.add_hooks("codex")
        command = json.loads((self.home / ".codex/hooks.json").read_text())["hooks"]["Stop"][0]["hooks"][0]
        self.assertIn("--hook stop codex", command["command"])
        self.assertTrue(cli.remove_hooks("codex"))
        self.assertFalse((self.home / ".codex/hooks.json").exists())

    def test_copilot_gets_its_own_hook_file(self):
        (self.home / ".copilot").mkdir()
        cli.add_hooks("copilot")
        data = json.loads((self.home / ".copilot/hooks/agent-awake.json").read_text())
        self.assertEqual(data["version"], 1)
        self.assertIn("--hook start copilot", data["hooks"]["userPromptSubmitted"][0]["bash"])
        self.assertEqual(cli.hooks_state("copilot"), "installed")
        self.assertEqual(cli.hooks_state("claude"), "absent")


class DaemonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home = root / "home"
        self.holds = self.home / ".cache/agent-awake/holds"
        self.wakes = self.home / ".cache/agent-awake/wake"
        self.holds.mkdir(parents=True)
        self.wakes.mkdir()
        self.stub = root / "stub"
        bin_dir = root / "bin"
        self.stub.mkdir()
        bin_dir.mkdir()
        for name, body in STUBS.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.environment = {**os.environ, "STUB": str(self.stub), "AGENT_AWAKE_TEST_PATH": str(bin_dir)}
        self.set_state(disabled=0, power=ON_ADAPTER, lid="")

    def set_state(self, disabled=None, power=None, lid=None, sched=""):
        if disabled is not None:
            (self.stub / "settings").write_text(f" SleepDisabled\t\t{disabled}\n")
        if power is not None:
            (self.stub / "batt").write_text(power)
        if lid is not None:
            (self.stub / "ioreg").write_text(lid)
        (self.stub / "sched").write_text(sched)
        (self.stub / "calls").write_text("")

    def reconcile(self, battery=20):
        (self.stub / "calls").write_text("")
        result = subprocess.run(["/bin/bash", str(DAEMON), "--reconcile", "--home", str(self.home),
                                 "--battery", str(battery)], env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line for line in (self.stub / "calls").read_text().splitlines()
                if not line.startswith("-g")], result.stdout

    def hold(self, name, content, age=0):
        path = self.holds / name
        path.write_text(content)
        os.utime(path, (time.time() - age,) * 2)

    def test_a_working_agent_disables_sleep_once(self):
        self.hold("claude-a", f"{os.getpid()}\n")
        calls, log = self.reconcile()
        self.assertEqual(calls, ["-a disablesleep 1"])
        self.assertIn("Keeping the Mac awake for claude-a.", log)
        self.assertEqual(self.reconcile()[0], [])

    def test_holds_that_no_longer_count_do_not_disable_sleep(self):
        finished = subprocess.Popen(["/usr/bin/true"])
        finished.wait()
        self.hold("exited", f"{finished.pid}\n")
        self.hold("expired", f"{os.getpid()}\n", age=1300)
        self.hold("released", "released\n", age=60)
        self.assertEqual(self.reconcile()[0], [])

    def test_a_just_released_turn_still_counts(self):
        self.hold("copilot-a", "released\n", age=5)
        self.assertEqual(self.reconcile()[0], ["-a disablesleep 1"])

    def test_a_long_tool_call_keeps_the_hold_for_two_hours(self):
        self.hold("claude-build", f"{os.getpid()}\nbusy\n", age=1300)
        self.assertEqual(self.reconcile()[0], ["-a disablesleep 1"])
        self.set_state(disabled=0)
        self.hold("claude-build", f"{os.getpid()}\nbusy\n", age=7300)
        self.assertEqual(self.reconcile()[0], [])

    def test_release_with_the_lid_closed_sleeps_the_mac(self):
        self.set_state(disabled=1, lid=LID_CLOSED)
        calls, log = self.reconcile()
        self.assertEqual(calls, ["-a disablesleep 0", "sleepnow"])
        self.assertIn("The lid is closed; sleeping now.", log)

    def test_release_with_an_external_display_or_open_lid_does_not_sleep(self):
        for lid in (LID_CLOSED_WITH_DISPLAY, '"AppleClamshellState" = No\n'):
            with self.subTest(lid=lid):
                self.set_state(disabled=1, lid=lid)
                calls, log = self.reconcile()
                self.assertEqual(calls, ["-a disablesleep 0"])
                self.assertEqual("leaving sleep to macOS" in log, lid == LID_CLOSED_WITH_DISPLAY)

    def test_release_waits_for_the_closed_lid_to_cause_sleep_again(self):
        # In a dark wake, macOS takes a while to report that the lid causes sleep after disablesleep is cleared.
        self.set_state(disabled=1, lid=LID_CLOSED_WITH_DISPLAY)
        during = self.stub / "during-sleep"
        during.write_text(f"#!/bin/bash\necho x >> '{self.stub}/slept'\n"
                          f"[ $(wc -l < '{self.stub}/slept') -ge 3 ] && printf '%s' '{LID_CLOSED}' > '{self.stub}/ioreg'\n"
                          "exit 0\n")
        during.chmod(0o755)
        calls, log = self.reconcile()
        self.assertEqual(calls, ["-a disablesleep 0", "sleepnow"])
        self.assertIn("The lid is closed; sleeping now.", log)

    def test_low_battery_allows_sleep_despite_a_hold(self):
        self.hold("codex-a", f"{os.getpid()}\n")
        self.set_state(disabled=1, power=ON_BATTERY.format(15), lid=LID_CLOSED)
        calls, log = self.reconcile(battery=20)
        self.assertEqual(calls, ["-a disablesleep 0", "sleepnow"])
        self.assertIn("Battery below 20%", log)
        self.set_state(disabled=0, power=ON_BATTERY.format(25))
        self.assertEqual(self.reconcile(battery=20)[0], ["-a disablesleep 1"])

    def test_wake_requests_are_scheduled_early_and_stale_ones_cancelled(self):
        stamp = int(time.time()) + 3600
        (self.wakes / f"codex-resume.{stamp}").touch()
        self.set_state(sched=" [0]  wake at 01/02/2030 10:00:00 by 'agent-awake'\n"
                             " [1]  wake at 01/03/2030 10:00:00 by 'someone-else'\n")
        # Several wakes, since a short dark wake can end before launchd starts the helper.
        wakes = [f"schedule wake {datetime.fromtimestamp(stamp - offset).strftime('%m/%d/%y %H:%M:%S')} agent-awake"
                 for offset in (90, 60, 30, 0)]
        self.assertEqual(self.reconcile()[0], [*wakes, "schedule cancel wake 01/02/30 10:00:00 agent-awake"])

    def test_a_wake_requested_while_the_helper_runs_is_scheduled_before_it_exits(self):
        # launchd does not start the helper again for a change made while it runs.
        stamp = int(time.time()) + 3600
        during = self.stub / "during-settings"
        during.write_text(f"#!/bin/bash\nrm -f '{during}'\ntouch '{self.wakes}/codex-resume.{stamp}'\n")
        during.chmod(0o755)
        calls = self.reconcile()[0]
        self.assertEqual([call for call in calls if call.startswith("schedule wake")],
                         [f"schedule wake {datetime.fromtimestamp(stamp - offset).strftime('%m/%d/%y %H:%M:%S')} "
                          "agent-awake" for offset in (90, 60, 30, 0)])

    def test_an_imminent_scheduled_wake_is_kept_and_its_window_keeps_the_mac_awake(self):
        stamp = int(time.time()) + 80
        (self.wakes / f"codex-resume.{stamp}").touch()
        listed = [datetime.fromtimestamp(stamp - offset).strftime("%m/%d/%Y %H:%M:%S") for offset in (60, 30, 0)]
        self.set_state(sched="".join(f" [{index}]  wake at {at} by 'agent-awake'\n" for index, at in enumerate(listed)))
        self.assertEqual(self.reconcile()[0], ["-a disablesleep 1"])

    def test_a_prompt_during_the_pause_before_sleeping_keeps_the_mac_awake(self):
        self.set_state(disabled=1, lid=LID_CLOSED)
        during = self.stub / "during-sleep"
        during.write_text(f"#!/bin/bash\necho {os.getpid()} > '{self.holds}/claude-late'\n")
        during.chmod(0o755)
        calls, log = self.reconcile()
        self.assertEqual(calls, ["-a disablesleep 0", "-a disablesleep 1"])
        self.assertIn("Keeping the Mac awake for claude-late.", log)

    def test_malformed_wake_requests_do_not_stop_the_battery_safeguard(self):
        # A leading zero would make Bash read the time as octal and abort the reconcile.
        (self.wakes / "bad.09999999999").touch()
        (self.wakes / "bad.12x").touch()
        self.hold("codex-a", f"{os.getpid()}\n")
        self.set_state(disabled=1, power=ON_BATTERY.format(10), lid=LID_CLOSED)
        self.assertEqual(self.reconcile()[0], ["-a disablesleep 0", "sleepnow"])

    def test_symlinked_holds_are_ignored(self):
        target = self.home / "elsewhere"
        target.write_text(f"{os.getpid()}\n")
        (self.holds / "link").symlink_to(target)
        self.assertEqual(self.reconcile()[0], [])

    def test_invalid_arguments_are_refused(self):
        for arguments in (["--reconcile", "--home", "relative", "--battery", "20"],
                          ["--reconcile", "--home", str(self.home), "--battery", "0"],
                          ["--reconcile", "--uninstall"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(["/bin/bash", str(DAEMON), *arguments], env=self.environment,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error:", result.stderr)
        self.assertEqual((self.stub / "calls").read_text(), "")


if __name__ == "__main__":
    unittest.main()
