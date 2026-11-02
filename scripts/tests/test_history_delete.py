"""Delete isolated zsh history events without changing unrelated saved entries."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_command_help import REPO, Sandbox


SPEC = importlib.util.spec_from_file_location("history_delete", REPO / "scripts/history-delete.py")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class HistoryFileTests(unittest.TestCase):
    def test_native_records_keep_timestamps_multiline_and_unrelated_bytes(self):
        original = (b": 100:2;keep\n: 101:4;multi\\\nline\n"
                    b": 102:0;keep trailing space \n: 103:0;tail\\ \n")
        self.assertEqual(
            HELPER.filtered(original, b"multi\nline", required=True),
            b": 100:2;keep\n: 102:0;keep trailing space \n: 103:0;tail\\ \n",
        )
        self.assertEqual(HELPER.command(b": 102:0;keep trailing space \n"), b"keep trailing space ")
        self.assertEqual(HELPER.command(b": 103:0;tail\\ \n"), b"tail\\")

    def test_errors_leave_saved_history_unchanged_and_do_not_remove_foreign_lock(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            saved, snapshot, target = (root / name for name in ("saved", "snapshot", "target"))
            original = b": 100:0;remove\n: 101:0;keep\n"
            saved.write_bytes(original)
            snapshot.write_bytes(original)
            target.write_bytes(b"remove")
            lock = Path(str(saved) + ".LOCK")
            lock.write_text("foreign lock")
            with self.assertRaisesRegex(ValueError, "locked"):
                HELPER.delete(saved, snapshot, target)
            self.assertEqual(lock.read_text(), "foreign lock")
            self.assertEqual(saved.read_bytes(), original)
            lock.unlink()
            saved.write_bytes(original + b": 102:0;remove\n")
            with self.assertRaisesRegex(ValueError, "multiple saved copies"):
                HELPER.delete(saved, snapshot, target)
            self.assertEqual(saved.read_bytes(), original + b": 102:0;remove\n")
            self.assertFalse(lock.exists())
            with self.assertRaisesRegex(ValueError, "could not be matched"):
                HELPER.filtered(original, b"absent", required=True)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                HELPER.records(b": 100:0;unfinished\\\n")

    def test_atomic_update_preserves_permissions_and_unloaded_events(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            saved, snapshot, target = (root / name for name in ("saved", "snapshot", "target"))
            saved.write_bytes(b": 99:1;not loaded\n: 100:2;remove\n: 101:3;keep\n")
            saved.chmod(0o600)
            snapshot.write_bytes(b": 100:2;remove\n: 101:3;keep\n")
            target.write_bytes(b"remove")
            HELPER.delete(saved, snapshot, target)
            self.assertEqual(saved.read_bytes(), b": 99:1;not loaded\n: 101:3;keep\n")
            self.assertEqual(snapshot.read_bytes(), b": 101:3;keep\n")
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual({path.name for path in root.iterdir()}, {"saved", "snapshot", "target"})


@unittest.skipUnless(shutil.which("zsh"), "zsh is not installed")
class HistoryShellTests(unittest.TestCase):
    def run_shell(self, commands, history=b": 100:0;keep one\n: 101:0;remove me\n: 102:0;keep two\n",
                  answer=b"y"):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            saved = sandbox.home / ".zsh_history"
            saved.write_bytes(history)
            response = sandbox.root / "answer"
            response.write_bytes(answer)
            commands = commands.replace("history-delete 2\n", f'history-delete 2 < "{response}"\n')
            script = (
                'source "$HOME/.dotfiles/zsh/conf.d/history.zsh"\n'
                'setopt extendedhistory\n'
                'fc -p "$HISTFILE"\n'
                + commands + "\n"
            )
            result = subprocess.run(
                ["zsh", "-f", "-i"], input=script, cwd=sandbox.project,
                env={**sandbox.env, "TMPDIR": str(sandbox.root)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(sandbox.tool_calls(), "")
            leftovers = list(sandbox.root.glob("history-delete.*"))
            self.assertEqual(leftovers, [])
            return result, saved.read_bytes()

    def test_deletion_removes_from_live_and_saved_history_and_stays_deleted(self):
        result, saved = self.run_shell(
            'history-delete 2\n'
            'print -r -- LIVE-BEGIN\n'
            'fc -l -n 1\n'
            'print -r -- LIVE-END\n'
            'print -s -- "new event"\n'
            'fc -W\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"✓ Event 2 +Deleted")
        self.assertNotIn("─ History", result.stdout)
        self.assertNotIn("Selected for deletion", result.stdout)
        self.assertNotIn("Event numbers may change", result.stdout)
        self.assertNotIn("Other open shells", result.stdout)
        live = result.stdout.split("LIVE-BEGIN\n", 1)[1].split("LIVE-END", 1)[0]
        self.assertIn("keep one", live)
        self.assertIn("keep two", live)
        self.assertNotIn("remove me", live)
        self.assertNotIn(b";remove me\n", saved)
        self.assertIn(b";keep one\n", saved)
        self.assertIn(b";keep two\n", saved)
        self.assertIn(b";new event\n", saved)

    def test_multiline_event_is_removed_without_deleting_its_neighbors(self):
        result, saved = self.run_shell(
            'history-delete 2\nfc -l -n 1\n',
            b": 100:0;keep one\n: 101:0;multi\\\nline\n: 102:0;keep two\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"✓ Event 2 +Deleted")
        self.assertRegex(result.stdout, r"Event 2 +multi\n    line\n")
        self.assertNotIn(b";multi\\\nline\n", saved)
        self.assertIn(b";keep one\n", saved)
        self.assertIn(b";keep two\n", saved)

    def test_unicode_event_and_trailing_space_neighbor_are_preserved_correctly(self):
        result, saved = self.run_shell(
            'history-delete 2\nfc -l -n 1\n',
            ": 100:0;keep trailing space \n: 101:0;fjern æøå\n: 102:0;behold ø\n".encode(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"✓ Event 2 +Deleted")
        self.assertRegex(result.stdout, r"Event 2 +fjern æøå\n")
        self.assertNotIn("fjern æøå".encode(), saved)
        self.assertIn(b";keep trailing space \n", saved)
        self.assertIn("behold ø".encode(), saved)

    def test_confirmation_displays_target_and_cancellation_preserves_history(self):
        for answer in (b"n", b"\n", b"", b"x", b"\x1b"):
            with self.subTest(answer=answer):
                result, saved = self.run_shell(
                    'history-delete 2\nprint -r -- STATUS:$?\n'
                    'print -r -- LIVE-BEGIN\nfc -l -n 1\n', answer=answer,
                )
                self.assertRegex(result.stdout, r"Event 2 +remove me\n")
                self.assertIn("Delete this history entry? [y/N]", result.stdout)
                self.assertIn("STATUS:0", result.stdout)
                self.assertNotIn("Deleted", result.stdout)
                self.assertIn("remove me", result.stdout.split("LIVE-BEGIN\n", 1)[1])
                self.assertIn(b";remove me\n", saved)

    def test_help_unknown_events_and_invalid_arguments_do_not_delete(self):
        for args, status in (("--help", 0), ("-h", 0), ("", 2), ("abc", 2), ("0", 2), ("99999", 1)):
            with self.subTest(args=args):
                result, saved = self.run_shell(f'history-delete {args}\nprint -r -- STATUS:$?\n')
                self.assertIn(f"STATUS:{status}", result.stdout)
                self.assertIn(b";remove me\n", saved)
                if status:
                    self.assertIn("Error:", result.stderr)
                else:
                    self.assertIn("Usage: history-delete", result.stdout)


if __name__ == "__main__":
    unittest.main()
