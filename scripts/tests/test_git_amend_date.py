"""Date-only amendments in disposable repositories; never touch the user's Git state."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/git-amend-date.sh"


class AmendDateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".dotfiles").symlink_to(REPO, target_is_directory=True)
        self.work = self.root / "repo"
        self.work.mkdir()
        self.env = {
            "HOME": str(self.home), "PATH": os.environ["PATH"], "TZ": "UTC",
            "LANG": "en_US.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Original Author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Original Committer", "GIT_COMMITTER_EMAIL": "committer@example.invalid",
        }
        self.git("init", "-q")
        alias = subprocess.check_output(
            ["git", "config", "--file", str(REPO / "git/gitconfig"), "alias.amend-date"],
            env=self.env, text=True,
        ).strip()
        self.git("config", "alias.amend-date", alias)
        self.git("config", "core.logAllRefUpdates", "true")
        self.commit("parent", 1700000000, 1700000600)
        self.commit("tip", 1700100000, 1700100600)

    def git(self, *args, data=None, env=None):
        result = subprocess.run(
            ["git", *args], cwd=self.work, env={**self.env, **(env or {})},
            input=data, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def commit(self, content, author, committer):
        (self.work / "file").write_text(content)
        self.git("add", "file")
        self.git("commit", "-qm", content, env={
            "GIT_AUTHOR_DATE": f"@{author} +0100", "GIT_COMMITTER_DATE": f"@{committer} +0200",
        })

    def run_amend(self, *args, answer="y", expected=0, env=None):
        result = subprocess.run(
            ["git", "amend-date", *args], cwd=self.work,
            env={**self.env, **(env or {})}, input=answer, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def dates(self):
        return [int(value) for value in self.git("show", "-s", "--format=%at %ct").split()]

    def test_explicit_date_changes_only_headers_preserving_index_message_and_identity(self):
        before = self.git("cat-file", "commit", "HEAD")
        old = self.git("rev-parse", "HEAD")
        (self.work / "file").write_text("staged change")
        self.git("add", "file")
        (self.work / "file").write_text("unstaged change")
        index = (self.work / ".git/index").read_bytes()
        status = self.git("status", "--porcelain")
        result = self.run_amend("2023-11-16T12:00:00+01:00", env={
            "GIT_AUTHOR_NAME": "Different Author", "GIT_COMMITTER_NAME": "Different Committer",
        })
        self.assertIn("16. november 2023, kl. 12.00.00 +0100", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), old)
        after = self.git("cat-file", "commit", "HEAD")
        def without_dates(raw):
            return re.sub(rb"(?m)^((?:author|committer) .*?) [0-9]+ [+-][0-9]{4}$", rb"\1", raw)
        self.assertEqual(without_dates(after), without_dates(before))
        self.assertEqual(self.dates(), [1700132400, 1700132400])
        self.assertEqual((self.work / ".git/index").read_bytes(), index)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertEqual((self.work / "file").read_text(), "unstaged change")

    def test_duration_is_ago_from_now_and_shared_units_are_supported(self):
        for age, seconds in (("30m", 1800), ("2h", 7200), ("1d", 86400), ("2w", 1209600)):
            with self.subTest(age=age):
                start = int(time.time())
                self.run_amend(age)
                end = int(time.time())
                author, committer = self.dates()
                self.assertEqual(author, committer)
                self.assertLessEqual(start - seconds, author)
                self.assertLessEqual(author, end - seconds)

    def test_shift_subtracts_from_each_date_and_keeps_offsets(self):
        self.run_amend("--shift", "1d")
        self.assertEqual(self.dates(), [1700013600, 1700014200])
        self.assertEqual(self.git("show", "-s", "--format=%ai %ci").strip(),
                         b"2023-11-15 03:00:00 +0100 2023-11-15 04:10:00 +0200")

    def test_earlier_parent_warning_appears_before_confirmation(self):
        result = self.run_amend("--shift", "2d", answer="n")
        self.assertEqual(result.stdout.count("Earlier than parent"), 2)
        self.assertLess(result.stdout.index("Earlier than parent"),
                        result.stdout.index("Change these commit dates?"))
        self.assertEqual(self.dates(), [1700100000, 1700100600])
        self.assertNotIn("Updated", result.stdout)

    def test_cancellation_and_bad_dates_leave_head_and_index_unchanged(self):
        old = self.git("rev-parse", "HEAD")
        index = (self.work / ".git/index").read_bytes()
        for answer in ("n", "\n", "", "x"):
            self.run_amend("1d", answer=answer)
            self.assertEqual(self.git("rev-parse", "HEAD"), old)
        for args, status in (
            ((), 2), (("1",), 2), (("1y",), 2), (("--shift", "tomorrow"), 2),
            (("--shift", "999999999d"), 2), (("not a date",), 1),
            (("--unknown",), 2), (("--shift", "1d", "HEAD", "extra"), 2),
            (("1d", "no-such-commit"), 1), (("1d", "--all"), 2), (("1d", ""), 2),
        ):
            with self.subTest(args=args):
                result = self.run_amend(*args, expected=status)
                self.assertIn("Error:", result.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD"), old)
                self.assertEqual((self.work / ".git/index").read_bytes(), index)

    def test_signed_commit_is_refused_without_losing_signature(self):
        raw = self.git("cat-file", "commit", "HEAD")
        headers, message = raw.split(b"\n\n", 1)
        signed = headers + b"\ngpgsig -----BEGIN PGP SIGNATURE-----\n test\n -----END PGP SIGNATURE-----\n\n" + message
        oid = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=signed).strip().decode()
        self.git("update-ref", "HEAD", oid)
        result = self.run_amend("1d", expected=1)
        self.assertIn("signed commits are not supported", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip().decode(), oid)

    def test_hash_selection_rewrites_only_current_branch_preserving_descendants_and_index(self):
        selected = self.git("rev-parse", "HEAD").strip()
        original = self.git("cat-file", "commit", "HEAD")
        self.commit("descendant one", 1700200000, 1700200600)
        first = self.git("cat-file", "commit", "HEAD")
        self.commit("descendant two", 1700300000, 1700300600)
        second = self.git("cat-file", "commit", "HEAD")
        tip = self.git("rev-parse", "HEAD")
        self.git("branch", "untouched")
        (self.work / "file").write_text("staged")
        self.git("add", "file")
        (self.work / "file").write_text("working")
        index = (self.work / ".git/index").read_bytes()
        result = self.run_amend("--shift", "1h", selected[:7].decode())
        self.assertIn("Rewrites 3 hashes", result.stdout)
        self.assertIn("other branches stay unchanged", result.stdout)
        self.assertEqual(self.git("rev-parse", "untouched"), tip)
        self.assertEqual(self.git("cat-file", "commit", "HEAD~2"),
                         original.replace(b"1700100000", b"1700096400")
                                 .replace(b"1700100600", b"1700097000"))
        for revision, before in (("HEAD~1", first), ("HEAD", second)):
            after = self.git("cat-file", "commit", revision)
            self.assertNotEqual(after, before)
            self.assertEqual(re.sub(rb"(?m)^parent [0-9a-f]+$", b"parent", after),
                             re.sub(rb"(?m)^parent [0-9a-f]+$", b"parent", before))
        self.assertEqual((self.work / ".git/index").read_bytes(), index)
        self.assertEqual((self.work / "file").read_text(), "working")

    def test_older_hash_accepts_explicit_dates_and_cancellation_does_not_move_refs(self):
        old = self.git("rev-parse", "HEAD")
        result = self.run_amend("1d", "HEAD~1", answer="n")
        self.assertIn("Rewrites 2 hashes", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), old)
        self.run_amend("2023-11-16T12:00:00+01:00", "HEAD~1")
        self.assertEqual(self.git("show", "-s", "--format=%at %ct", "HEAD~1").strip(),
                         b"1700132400 1700132400")
        self.assertEqual(self.dates(), [1700100000, 1700100600])

    def test_older_rewrites_refuse_merges_detached_heads_and_unrelated_commits(self):
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        unrelated = self.git("commit-tree", tree, data=b"unrelated\n").strip().decode()
        before = self.git("rev-parse", "HEAD")
        result = self.run_amend("1d", unrelated, expected=1)
        self.assertIn("not an ancestor", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.git("checkout", "--detach", "-q")
        result = self.run_amend("1d", "HEAD~1", expected=1)
        self.assertIn("checked-out branch", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.git("switch", "-qc", "merges")
        merge = self.git("commit-tree", tree, "-p", "HEAD", "-p", unrelated,
                         data=b"merge\n").strip().decode()
        self.git("update-ref", "HEAD", merge)
        for target in ("HEAD~1", merge):
            with self.subTest(target=target):
                if target == merge:
                    self.commit("after merge", 1700400000, 1700400600)
                tip = self.git("rev-parse", "HEAD")
                result = self.run_amend("1d", target, expected=1)
                self.assertIn("cannot include merges", result.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD"), tip)

    def test_signed_descendants_are_refused_before_confirmation(self):
        raw = self.git("cat-file", "commit", "HEAD")
        headers, message = raw.split(b"\n\n", 1)
        signed = headers + b"\ngpgsig fake\n signature\n\n" + message
        oid = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=signed).strip().decode()
        self.git("update-ref", "HEAD", oid)
        self.commit("unsigned tip", 1700200000, 1700200600)
        before = self.git("rev-parse", "HEAD")
        result = self.run_amend("1d", "HEAD~2", expected=1)
        self.assertIn("signed commits", result.stderr)
        self.assertNotIn("Change these commit dates?", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_pushed_warning_uses_remote_tracking_refs_for_selected_ancestor(self):
        # A local bare remote exercises publication without network access.
        remote = self.root / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", "HEAD:refs/heads/published")
        published = self.git("rev-parse", "refs/remotes/origin/published")
        self.commit("local only", 1700200000, 1700200600)
        local = self.run_amend("1d", answer="n")
        self.assertNotIn("Already pushed", local.stdout)
        result = self.run_amend("--shift", "1h", "HEAD~1", answer="n")
        self.assertIn("Already pushed", result.stdout)
        self.assertIn("origin/published", result.stdout)
        self.assertLess(result.stdout.index("Already pushed"),
                        result.stdout.index("Change these commit dates?"))
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/published"), published)
        self.git("update-ref", "-d", "refs/remotes/origin/published")
        unknown = self.run_amend("1d", answer="n")
        self.assertIn("Pushed status unknown", unknown.stdout)

    def test_root_and_detached_head_and_merge_parent_warning(self):
        self.git("checkout", "--detach", "-q", "HEAD~1")
        self.run_amend("--shift", "1h")
        self.assertEqual(self.dates(), [1699996400, 1699997000])
        parent = self.git("rev-parse", "HEAD").strip().decode()
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        merge = self.git("commit-tree", tree, "-p", parent, "-p", "HEAD@{1}",
                         data=b"merge\n", env={
                             "GIT_AUTHOR_DATE": "@1700200000 +0000",
                             "GIT_COMMITTER_DATE": "@1700200000 +0000",
                         }).strip().decode()
        self.git("update-ref", "HEAD", merge)
        result = self.run_amend("2023-11-14T22:13:20+00:00", answer="n")
        self.assertIn("Earlier than parent", result.stdout)

    def test_ref_update_failure_leaves_head_unchanged(self):
        # A reference-transaction hook rejects the final compare-and-swap.
        hook = self.work / ".git/hooks/reference-transaction"
        hook.write_text('#!/bin/sh\n[ "$1" != prepared ]\n')
        hook.chmod(0o755)
        before = self.git("rev-parse", "HEAD")
        result = self.run_amend("1d", expected=1)
        self.assertIn("Error:", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_head_or_branch_changes_during_confirmation_are_not_overwritten(self):
        spec = importlib.util.spec_from_file_location("amend_date", REPO / "scripts/git-amend-date.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_run = subprocess.run
        before = self.git("rev-parse", "HEAD").strip()
        parent = self.git("rev-parse", "HEAD^").strip()
        branch = self.git("symbolic-ref", "--short", "HEAD").strip().decode()
        self.git("branch", "other")
        for action in ("commit", "branch"):
            with self.subTest(action=action):
                def during_confirmation(args, **kwargs):
                    if args[0] == "/bin/bash":
                        command = (["git", "update-ref", "HEAD", parent.decode()]
                                   if action == "commit" else ["git", "checkout", "-q", "other"])
                        real_run(command, cwd=self.work, env=self.env, check=True, capture_output=True)
                        return subprocess.CompletedProcess(args, 0)
                    return real_run(args, **kwargs)
                previous = Path.cwd()
                try:
                    os.chdir(self.work)
                    with patch.dict(os.environ, self.env, clear=True), \
                            patch.object(module.subprocess, "run", side_effect=during_confirmation), \
                            redirect_stdout(io.StringIO()):
                        with self.assertRaises(ValueError):
                            module.amend("--shift-seconds", "3600")
                finally:
                    os.chdir(previous)
                expected = parent if action == "commit" else before
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), expected)
                self.git("checkout", "-q", branch)
                self.git("update-ref", "HEAD", before.decode())

    def test_help_requires_no_repository_or_git_execution(self):
        env = dict(self.env, PATH="/usr/bin:/bin")
        result = subprocess.run(["/bin/bash", str(SCRIPT), "--help"],
                                cwd=self.home, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("Usage: git amend-date"))
        self.assertIn("--shift", result.stdout)


if __name__ == "__main__":
    unittest.main()
