"""Regression tests for prune-branches.sh and its audit-repo section, using
real Git repositories with local bare remotes."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "prune-branches.sh"


class PruneBranchesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="prune-branches-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        home = self.root / "home"
        home.mkdir()
        (self.root / "tmp").mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key not in ("BASH_ENV", "ENV")}
        self.env.update(HOME=str(home), TMPDIR=str(self.root / "tmp"), NO_COLOR="1",
                        GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"),
                           ("init.defaultBranch", "main"), ("advice.detachedHead", "false")):
            self.git(self.root, "config", "--global", key, value)
        self.origin = self.root / "origin.git"
        self.git(self.root, "init", "--bare", "-q", str(self.origin))
        self.work = self.root / "dev" / "app"
        self.git(self.root, "clone", "-q", str(self.origin), str(self.work))
        self.commit(self.work, "base")
        self.git(self.work, "push", "-q", "origin", "main")
        self.git(self.work, "remote", "set-head", "origin", "-a")
        self.other = self.root / "other"
        self.git(self.root, "clone", "-q", str(self.origin), str(self.other))

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, cwd, name, text=None):
        (Path(cwd) / name).write_text(text or name)
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-q", "-m", name)
        return self.git(cwd, "rev-parse", "HEAD")

    def pushed_branch(self, name):
        self.git(self.work, "switch", "-q", "-c", name, "main")
        sha = self.commit(self.work, name)
        self.git(self.work, "push", "-q", "-u", "origin", name)
        self.git(self.work, "switch", "-q", "main")
        return sha

    def delete_on_remote(self, *names, merge=True):
        """Deletes remote branches, by default after merging them as a pull request would."""
        self.git(self.other, "fetch", "-q")
        for name in names:
            if merge:
                self.git(self.other, "merge", "-q", "--no-edit", "origin/" + name)
                self.git(self.other, "push", "-q", "origin", "main")
            self.git(self.other, "push", "-q", "origin", "--delete", name)

    def branches(self, cwd=None):
        return set(self.git(cwd or self.work, "branch", "--format=%(refname:short)").split())

    def run_prune(self, *args, cwd=None, answers=""):
        return subprocess.run(["/bin/bash", str(SCRIPT), *map(str, args)], cwd=cwd or self.work,
                              env=self.env, input=answers, text=True, capture_output=True, timeout=60)

    def test_dry_run_changes_nothing_and_later_apply_deletes_merged_branch(self):
        self.pushed_branch("merged")
        self.delete_on_remote("merged")

        dry = self.run_prune("--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("already in origin/main", dry.stdout)
        self.assertTrue(dry.stdout.startswith("Dry run — nothing will be changed."), dry.stdout)
        self.assertIn("merged", self.branches())

        applied = self.run_prune("--yes")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertNotIn("merged", self.branches())
        config = subprocess.run(["git", "config", "--get-regexp", "^branch\\.merged\\."], cwd=self.work,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(config.stdout, "")
        log = (self.work / ".git" / "prune-branches.log").read_text()
        self.assertIn("merged", log)

    def test_detached_removal_is_opt_in_and_fetches_untracked_remotes(self):
        detached = self.root / "detached"
        sha = self.git(self.work, "rev-parse", "origin/main")
        self.git(self.work, "worktree", "add", "-q", "--detach", str(detached), sha)
        self.git(self.work, "branch", "--unset-upstream", "main")
        default = self.run_prune("--yes")
        self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
        self.assertTrue(detached.exists())
        dry = self.run_prune("--include-detached", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("Detached to remove", dry.stdout)
        self.assertTrue(detached.exists())
        refs = self.git(self.work, "for-each-ref")
        no_fetch = self.run_prune("--include-detached", "--no-fetch", "--dry-run")
        self.assertEqual(no_fetch.returncode, 0, no_fetch.stdout + no_fetch.stderr)
        self.assertIn("Detached to remove", no_fetch.stdout)
        self.assertEqual(refs, self.git(self.work, "for-each-ref"))
        refused = self.run_prune("--include-detached", answers="n\n")
        self.assertEqual(refused.returncode, 0)
        self.assertTrue(detached.exists())
        result = self.run_prune("--include-detached", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(detached.exists())
        self.assertEqual(self.branches(), {"main"})
        self.assertIn("worktree:" + str(detached), (self.work / ".git/prune-branches.log").read_text())

    def test_detached_worktree_protections_and_head_history(self):
        locations = {}
        for name in ("dirty", "ignored", "locked", "history", "refs", "busy", "hidden"):
            path = self.root / name
            self.git(self.work, "worktree", "add", "-q", "--detach", str(path), "origin/main")
            locations[name] = path
        (self.work / ".git/info/exclude").write_text("secret\n")
        (locations["dirty"] / "local").write_text("keep")
        (locations["ignored"] / "secret").write_text("keep")
        self.git(self.work, "worktree", "lock", str(locations["locked"]))
        self.commit(locations["history"], "unpushed")
        self.git(locations["history"], "reset", "--hard", "origin/main")
        self.git(locations["refs"], "update-ref", "refs/worktree/keep", "HEAD")
        admin = Path(self.git(locations["busy"], "rev-parse", "--absolute-git-dir"))
        (admin / "rebase-merge").mkdir()
        self.git(locations["hidden"], "update-index", "--assume-unchanged", "base")
        (locations["hidden"] / "base").write_text("edit")
        result = self.run_prune("--include-detached", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in locations.values():
            self.assertTrue(path.exists(), result.stdout)
        for reason in ("uncommitted", "ignored local files", "locked", "HEAD history",
                       "ref(s) only in this worktree", "unfinished Git operation", "assume-unchanged"):
            self.assertIn(reason, result.stdout)

    def test_detached_unconfirmed_and_failed_fetch_are_kept(self):
        path = self.root / "detached"
        self.git(self.work, "worktree", "add", "-q", "--detach", str(path), "origin/main")
        self.commit(path, "unpushed")
        result = self.run_prune("--include-detached", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not confirmed on a remote", result.stdout)
        self.assertTrue(path.exists())
        self.git(path, "reset", "--hard", "origin/main")
        self.git(self.work, "remote", "set-url", "origin", str(self.root / "missing.git"))
        failed = self.run_prune("--include-detached", "--yes")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertTrue(path.exists())

    def test_detached_apply_rechecks_identity_and_lock(self):
        path = self.root / "detached"
        sha = self.git(self.work, "rev-parse", "origin/main")
        self.git(self.work, "worktree", "add", "-q", "--detach", str(path), sha)
        script = (
            'source "$1/scripts/lib/clean-targets.sh"; source "$1/scripts/lib/stale-branches.sh"; '
            'SB_GIT_DIR=$2; SB_TMP=$3; sb_apply_detached "$5" "$4" test ""'
        )
        for lock in (False, True):
            if lock:
                self.git(path, "switch", "--detach", sha)
                self.git(self.work, "worktree", "lock", str(path))
            else:
                self.commit(path, "changed")
            result = subprocess.run(
                ["/bin/bash", "-c", script, "test", str(REPO), str(self.work / ".git"),
                 str(self.root / "tmp"), sha, str(path)],
                env=self.env, text=True, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(path.exists())
            self.assertIn("locked" if lock else "no longer detached", result.stdout)

    def test_older_than_retains_recent_tips_and_scans_worktree_descendants(self):
        self.env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
        self.pushed_branch("old")
        self.pushed_branch("old-with-worktree")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        self.pushed_branch("recent")
        path = self.root / "old-worktree"
        self.git(self.work, "worktree", "add", "-q", str(path), "old-with-worktree")
        self.delete_on_remote("old", "old-with-worktree", "recent")
        result = self.run_prune("--older-than", "7d", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.branches(), {"main", "old-with-worktree", "recent"})
        self.assertIn("retained by --older-than", result.stdout)
        stamp = time.time() - 10 * 86400
        for child in path.iterdir():
            if child.name != ".git":
                os.utime(child, (stamp, stamp))
        os.utime(path, (stamp, stamp))
        applied = self.run_prune("--older-than", "7d", "--yes")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertFalse(path.exists())
        self.assertEqual(self.branches(), {"main", "recent"})

    def test_detached_age_guard_and_invoking_worktree(self):
        self.env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
        sha = self.commit(self.work, "old-tip")
        self.git(self.work, "push", "-q", "origin", "main")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        path = self.root / "detached"
        self.git(self.work, "worktree", "add", "-q", "--detach", str(path), sha)
        recent = self.run_prune("--include-detached", "--older-than", "7d", "--yes")
        self.assertEqual(recent.returncode, 0, recent.stdout + recent.stderr)
        self.assertIn("retained by --older-than", recent.stdout)
        self.assertTrue(path.exists())
        stamp = time.time() - 10 * 86400
        for child in path.iterdir():
            if child.name != ".git":
                os.utime(child, (stamp, stamp))
        os.utime(path, (stamp, stamp))
        invoked = self.run_prune("--include-detached", "--yes", cwd=path)
        self.assertEqual(invoked.returncode, 0, invoked.stdout + invoked.stderr)
        self.assertTrue(path.exists())
        old = self.run_prune("--include-detached", "--older-than", "7d", "--yes")
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        self.assertFalse(path.exists())

    def test_detached_stale_remote_ref_and_newline_path_are_never_removed(self):
        sha = self.pushed_branch("only-copy")
        path = self.root / "detached"
        odd = self.root / "line\nbreak"
        self.git(self.work, "worktree", "add", "-q", "--detach", str(path), sha)
        self.git(self.work, "worktree", "add", "-q", "--detach", str(odd), "origin/main")
        self.git(self.work, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
        self.git(self.other, "push", "-q", "origin", "--delete", "only-copy")
        self.git(self.work, "branch", "-D", "only-copy")
        result = self.run_prune("--include-detached", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(path.exists())
        self.assertTrue(odd.exists())
        self.assertIn("not confirmed on a remote branch", result.stdout)
        self.assertIn("path cannot be represented", result.stdout)

    def test_age_check_failure_retains_candidate_and_invalid_age_fails_before_fetch(self):
        self.pushed_branch("candidate")
        self.delete_on_remote("candidate")
        bin_path = self.root / "bin"
        bin_path.mkdir()
        python = bin_path / "python3"
        python.write_text("#!/bin/sh\nprintf 'age metadata unreadable\\n'\nexit 2\n")
        python.chmod(0o755)
        self.env["PATH"] = str(bin_path) + os.pathsep + self.env["PATH"]
        result = self.run_prune("--older-than", "7d", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("age metadata unreadable", result.stdout)
        self.assertIn("candidate", self.branches())
        for args in (("--older-than",), ("--older-than", "-7d"), ("--older-than", "bad")):
            result = self.run_prune(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Error:", result.stderr)

    def test_branch_age_is_rechecked_before_apply(self):
        self.env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
        sha = self.pushed_branch("planned-old")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        path = self.root / "planned-worktree"
        self.git(self.work, "worktree", "add", "-q", str(path), "planned-old")
        script = (
            'source "$1/scripts/lib/clean-targets.sh"; source "$1/scripts/lib/stale-branches.sh"; '
            'SB_GIT_DIR=$2; SB_TMP=$3; SB_OLDER_THAN=7d; '
            'sb_apply_delete planned-old "$4" "$5" test'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", script, "test", str(REPO), str(self.work / ".git"),
             str(self.root / "tmp"), sha, str(path)],
            env=self.env, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retained by --older-than", result.stdout)
        self.assertTrue(path.exists())
        self.assertIn("planned-old", self.branches())

    def test_never_pushed_unconfirmed_and_protected_branches_are_kept(self):
        self.git(self.work, "switch", "-q", "-c", "local-only", "main")
        self.commit(self.work, "local")
        self.git(self.work, "switch", "-q", "main")
        self.pushed_branch("extra")
        self.git(self.work, "switch", "-q", "extra")
        self.commit(self.work, "unpushed")
        self.git(self.work, "switch", "-q", "main")
        self.pushed_branch("develop")
        self.delete_on_remote("extra", "develop")

        result = self.run_prune("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.branches(), {"main", "local-only", "extra", "develop"})
        self.assertIn("not found on any remote", result.stdout)
        self.assertIn("protected branch", result.stdout)
        self.assertIn("Nothing to delete", result.stdout)

    def test_squash_merged_and_pull_request_heads_are_confirmed_without_snapshot(self):
        self.git(self.work, "switch", "-q", "-c", "squashed", "main")
        self.commit(self.work, "one")
        self.commit(self.work, "two")
        self.git(self.work, "push", "-q", "-u", "origin", "squashed")
        self.git(self.work, "switch", "-q", "main")
        pr_sha = self.pushed_branch("reviewed")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--squash", "origin/squashed")
        self.git(self.other, "commit", "-q", "-m", "squash")
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.origin, "update-ref", "refs/pull/7/head", pr_sha)
        # Deleting through this clone also removes its remote-tracking refs, so no snapshot exists.
        self.git(self.work, "push", "-q", "origin", "--delete", "squashed", "reviewed")

        result = self.run_prune("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("squash-merged into origin/main", result.stdout)
        self.assertIn("pushed as pull/7 on origin", result.stdout)
        self.assertEqual(self.branches(), {"main"})

    def test_worktrees_outside_repository_are_removed_only_when_safe(self):
        locations = {}
        for name in ("clean", "dirty", "secret", "generated"):
            self.pushed_branch(name)
            locations[name] = self.root / "agent-tool" / name / "app"
            self.git(self.work, "worktree", "add", "-q", str(locations[name]), name)
        (self.work / ".git" / "info" / "exclude").write_text("node_modules/\nlocal.secret\n")
        (locations["dirty"] / "dirty").write_text("changed")
        (locations["secret"] / "local.secret").write_text("token")
        (locations["generated"] / "node_modules" / "pkg").mkdir(parents=True)
        detached = self.root / "agent-tool" / "detached" / "app"
        self.git(self.work, "worktree", "add", "-q", "--detach", str(detached), "origin/main")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("clean", "dirty", "secret", "generated")

        result = self.run_prune("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.branches(), {"main", "dirty", "secret"}, result.stdout)
        self.assertFalse(locations["clean"].exists())
        self.assertFalse(locations["generated"].exists())
        self.assertEqual((locations["dirty"] / "dirty").read_text(), "changed")
        self.assertEqual((locations["secret"] / "local.secret").read_text(), "token")
        self.assertTrue(detached.exists())
        self.assertIn("1 uncommitted change(s)", result.stdout)
        self.assertIn("ignored local files: local.secret", result.stdout)
        self.assertIn("detached worktree, clean", result.stdout)

    def test_unpushed_merge_commit_is_not_treated_as_rebased(self):
        sha = self.pushed_branch("feat")
        side = self.pushed_branch("side")
        # Every non-merge commit is on main, so only the merge can hold unpushed changes.
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "cherry-pick", sha, side)
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "fetch", "-q")
        self.git(self.work, "switch", "-q", "feat")
        self.git(self.work, "merge", "-q", "--no-ff", "--no-commit", "side")
        (self.work / "local-only").write_text("unpushed")
        self.git(self.work, "add", "local-only")
        self.git(self.work, "commit", "-q", "-m", "merge with local change")
        self.git(self.work, "switch", "-q", "main")
        self.delete_on_remote("feat")

        result = self.run_prune("--yes")
        self.assertNotIn("rebased into", result.stdout)
        self.assertIn("feat", self.branches())

    def test_rebase_and_squash_evidence_keeps_whitespace_changes(self):
        self.git(self.work, "switch", "-q", "-c", "picked", "main")
        picked = [self.commit(self.work, "p1"), self.commit(self.work, "p2")]
        self.git(self.work, "push", "-q", "-u", "origin", "picked")
        self.git(self.work, "switch", "-q", "-c", "amended", "main")
        amended = self.commit(self.work, "m.py", "if x:\n    a()\nb()\n")
        self.git(self.work, "push", "-q", "-u", "origin", "amended")
        self.git(self.work, "switch", "-q", "-c", "squashy", "main")
        self.commit(self.work, "s.py", "if x:\n    a()\nb()\n")
        self.git(self.work, "push", "-q", "-u", "origin", "squashy")
        self.git(self.other, "fetch", "-q")
        self.commit(self.other, "unrelated")
        self.git(self.other, "cherry-pick", *picked, amended)
        self.git(self.other, "merge", "-q", "--squash", "origin/squashy")
        self.git(self.other, "commit", "-q", "-m", "squash")
        self.git(self.other, "push", "-q", "origin", "main")
        # Unpushed edits that differ only in indentation still change behaviour.
        self.commit(self.work, "s.py", "if x:\n    a()\n    b()\n")
        self.git(self.work, "switch", "-q", "amended")
        (self.work / "m.py").write_text("if x:\n    a()\n    b()\n")
        self.git(self.work, "commit", "-q", "-a", "--amend", "--no-edit")
        self.git(self.work, "switch", "-q", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "picked", "amended", "squashy")

        result = self.run_prune("--yes")
        self.assertIn("rebased into origin/main", result.stdout)
        self.assertEqual(self.branches(), {"main", "amended", "squashy"})

    def test_squash_evidence_includes_submodule_pointer_changes(self):
        self.git(self.work, "config", "diff.submodule", "log")
        self.git(self.work, "switch", "-q", "-c", "linked", "main")
        self.commit(self.work, "z-file")
        self.git(self.work, "push", "-q", "-u", "origin", "linked")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--squash", "origin/linked")
        self.git(self.other, "commit", "-q", "-m", "squash")
        self.git(self.other, "push", "-q", "origin", "main")
        # An unpushed gitlink sorting before the file renders as a summary patch-id would skip.
        self.git(self.work, "update-index", "--add", "--cacheinfo",
                 "160000," + self.git(self.work, "rev-parse", "main") + ",a-sub")
        self.git(self.work, "commit", "-q", "-m", "bump submodule")
        self.git(self.work, "switch", "-q", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "linked")

        result = self.run_prune("--yes")
        self.assertNotIn("squash-merged", result.stdout)
        self.assertIn("linked", self.branches())

    def test_branch_with_rebase_in_progress_is_kept(self):
        sha = self.pushed_branch("rebasing")
        location = self.root / "elsewhere" / "rebasing"
        self.git(self.work, "worktree", "add", "-q", str(location), "rebasing")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("rebasing")
        stopped = subprocess.run(["git", "rebase", "--force-rebase", "--exec", "false", "main"],
                                 cwd=location, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(stopped.returncode, 0)

        planned = self.run_prune("--dry-run")
        self.assertIn("rebase in progress in", planned.stdout)
        result = self.run_prune("--yes")
        self.assertIn("rebase in progress in", result.stdout)
        self.assertIn("rebasing", self.branches())
        script = (
            'source "$1/scripts/lib/clean-targets.sh"; source "$1/scripts/lib/stale-branches.sh"; '
            'SB_GIT_DIR=$2; SB_TMP=$3; sb_apply_delete rebasing "$4" "" test'
        )
        applied = subprocess.run(
            ["/bin/bash", "-c", script, "test", str(REPO), str(self.work / ".git"),
             str(self.root / "tmp"), sha],
            env=self.env, text=True, capture_output=True, timeout=30,
        )
        self.assertNotEqual(applied.returncode, 0)
        self.assertIn("rebase in progress in", applied.stdout)
        self.git(location, "rebase", "--continue")
        self.assertEqual(self.git(location, "branch", "--show-current"), "rebasing")

    def test_stale_tracking_refs_of_unreachable_remotes_are_not_evidence(self):
        fork = self.root / "fork.git"
        self.git(self.root, "init", "--bare", "-q", str(fork))
        self.git(self.work, "remote", "add", "fork", str(fork))
        self.pushed_branch("feat")
        self.git(self.work, "switch", "-q", "feat")
        self.commit(self.work, "fork-only")
        self.git(self.work, "push", "-q", "fork", "feat")
        self.git(self.work, "switch", "-q", "-c", "other", "main")
        self.git(self.work, "push", "-q", "-u", "fork", "other")
        self.git(self.work, "switch", "-q", "main")
        # The fork is gone, so its old tracking ref is the only copy of the commit.
        subprocess.run(["rm", "-rf", str(fork)], check=True)
        self.git(self.origin, "update-ref", "-d", "refs/heads/feat")

        result = self.run_prune("--yes")
        self.assertNotIn("already in fork/feat", result.stdout)
        self.assertIn("feat", self.branches())

    def test_squash_merged_branch_is_confirmed_after_later_upstream_edits(self):
        self.git(self.work, "switch", "-q", "-c", "bump", "main")
        self.commit(self.work, "version", "2")
        self.commit(self.work, "notes", "bumped")
        self.git(self.work, "push", "-q", "-u", "origin", "bump")
        self.git(self.work, "switch", "-q", "main")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--squash", "origin/bump")
        self.git(self.other, "commit", "-q", "-m", "squash")
        self.commit(self.other, "version", "3")
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "bump")

        result = self.run_prune("--yes")
        self.assertIn("squash-merged into origin/main", result.stdout)
        self.assertEqual(self.branches(), {"main"})

    def test_rebased_evidence_requires_merging_to_change_nothing(self):
        block = "[svc]\nhost=a\nport=1\nretries=3\ntimeout=10\ndebug=false\n#\n"
        self.commit(self.work, "conf.ini", "#\n" + block + block)
        self.git(self.work, "push", "-q", "origin", "main")
        self.git(self.work, "switch", "-q", "-c", "feat", "main")
        lines = (self.work / "conf.ini").read_text().splitlines(keepends=True)
        lines[4] = "retries=5\n"
        first = self.commit(self.work, "conf.ini", "".join(lines))
        self.git(self.work, "push", "-q", "-u", "origin", "feat")
        # The same edit in the second, identical block has the same patch-id.
        lines[11] = "retries=5\n"
        self.commit(self.work, "conf.ini", "".join(lines))
        self.git(self.work, "switch", "-q", "main")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--ff-only", "origin/main")
        self.commit(self.other, "unrelated")
        self.git(self.other, "cherry-pick", first)
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "feat")

        result = self.run_prune("--yes")
        self.assertNotIn("rebased into", result.stdout)
        self.assertIn("feat", self.branches())

    def test_squash_evidence_requires_merging_to_change_nothing(self):
        block = "[svc]\nhost=a\nport=1\nretries=3\ntimeout=10\ndebug=false\n#\n"
        self.commit(self.work, "conf.ini", "#\n" + block * 3)
        self.git(self.work, "push", "-q", "origin", "main")
        self.git(self.work, "switch", "-q", "-c", "feat", "main")
        lines = (self.work / "conf.ini").read_text().splitlines(keepends=True)
        lines[4] = "retries=5\n"
        self.commit(self.work, "conf.ini", "".join(lines))
        lines[5] = "timeout=20\n"
        self.commit(self.work, "conf.ini", "".join(lines))
        self.git(self.work, "push", "-q", "-u", "origin", "feat")
        self.git(self.work, "switch", "-q", "main")
        # The same squashed edit in the second, identical block has the same patch-id.
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--ff-only", "origin/main")
        upstream = (self.other / "conf.ini").read_text().splitlines(keepends=True)
        upstream[11] = "retries=5\n"
        upstream[12] = "timeout=20\n"
        self.commit(self.other, "conf.ini", "".join(upstream))
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "feat")

        result = self.run_prune("--yes")
        self.assertNotIn("squash-merged into", result.stdout)
        self.assertIn("feat", self.branches())

    def test_ignored_file_inside_tracked_build_folder_is_protected(self):
        (self.work / "build").mkdir()
        self.commit(self.work, "build/release.sh", "make")
        self.commit(self.work, ".gitignore", "*.keystore\n")
        self.git(self.work, "push", "-q", "origin", "main")
        self.pushed_branch("signed")
        location = self.root / "elsewhere" / "signed"
        self.git(self.work, "worktree", "add", "-q", str(location), "signed")
        (location / "build" / "release.keystore").write_text("secret")
        self.git(self.origin, "update-ref", "-d", "refs/heads/signed")

        result = self.run_prune("--yes")
        self.assertIn("ignored local files: build/release.keystore", result.stdout)
        self.assertEqual((location / "build" / "release.keystore").read_text(), "secret")
        self.assertIn("signed", self.branches())

    def test_worktree_history_outside_the_branch_blocks_removal(self):
        locations = {}
        for name in ("detached", "pinned", "amended"):
            self.pushed_branch(name)
            locations[name] = self.root / "elsewhere" / name
            self.git(self.work, "worktree", "add", "-q", str(locations[name]), name)
        self.git(locations["detached"], "switch", "-q", "--detach")
        self.commit(locations["detached"], "left-behind")
        self.git(locations["detached"], "switch", "-q", "detached")
        self.git(locations["pinned"], "update-ref", "refs/worktree/keep", "HEAD")
        # Rewriting the branch itself leaves only earlier branch states behind, which is fine.
        self.git(locations["amended"], "commit", "-q", "--amend", "-m", "reworded")
        self.git(locations["amended"], "push", "-q", "-f", "origin", "amended")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("detached", "pinned", "amended")

        result = self.run_prune("--yes")
        self.assertIn("1 commit(s) only in its HEAD history", result.stdout)
        self.assertIn("1 ref(s) only in this worktree", result.stdout)
        self.assertTrue(locations["detached"].exists())
        self.assertTrue(locations["pinned"].exists())
        self.assertFalse(locations["amended"].exists())
        self.assertEqual(self.branches(), {"main", "detached", "pinned"})

    def test_untracked_branch_that_exists_on_remote_is_not_called_never_pushed(self):
        self.env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
        self.pushed_branch("legacy")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "switch", "-q", "legacy")
        self.commit(self.other, "backport")
        self.git(self.other, "push", "-q", "origin", "legacy")
        self.git(self.work, "fetch", "-q")
        self.git(self.work, "branch", "--unset-upstream", "legacy")

        result = self.run_prune("--dry-run")
        self.assertIn("not tracking origin/legacy (0 ahead, 1 behind)", result.stdout)
        self.assertNotIn("never pushed", result.stdout)
        self.assertIn("legacy", self.branches())

    def test_default_branch_is_named_when_several_remote_branches_contain_the_commit(self):
        self.pushed_branch("feat")
        self.delete_on_remote("feat")
        self.git(self.other, "push", "-q", "origin", "main:aaa-later-work")
        self.git(self.work, "fetch", "-q", "origin", "aaa-later-work:refs/remotes/origin/aaa-later-work")

        for args in (("--dry-run",), ("--dry-run", "--no-fetch")):
            result = self.run_prune(*args)
            self.assertIn("already in origin/main", result.stdout, args)

    def test_merged_branch_whose_remote_branch_still_exists_is_reported_but_kept(self):
        self.pushed_branch("merged-kept")
        self.pushed_branch("open")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--no-edit", "origin/merged-kept")
        self.git(self.other, "push", "-q", "origin", "main")

        result = self.run_prune("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("merged into origin/main, but origin/merged-kept still exists", result.stdout)
        self.assertNotIn("open", result.stdout.split("─ Summary")[0])
        self.assertRegex(result.stdout, r"Checked +3 local branch\(es\) in 1 repository")
        self.assertNotIn("To delete", result.stdout)
        self.assertEqual(self.branches(), {"main", "merged-kept", "open"})

        worktree = self.root / "wt-merged-kept"
        self.git(self.work, "worktree", "add", "-q", str(worktree), "merged-kept")
        dry = self.run_prune("--include-merged", "--dry-run")
        self.assertIn("merged into origin/main; origin/merged-kept is kept", dry.stdout)
        self.assertTrue(dry.stdout.startswith("Dry run"))
        self.assertEqual(self.branches(), {"main", "merged-kept", "open"})

        applied = self.run_prune("--yes", "--include-merged")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(self.branches(), {"main", "open"})
        self.assertFalse(worktree.exists())
        self.assertIn("merged-kept", self.git(self.root, "--git-dir", str(self.origin), "branch"))

    def test_branch_tracking_a_local_branch_is_deleted_only_when_merged_and_included(self):
        self.pushed_branch("merged-local")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--no-edit", "origin/merged-local")
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "push", "-q", "origin", "--delete", "merged-local")
        self.git(self.work, "switch", "-q", "-c", "local-merged", "main")
        self.git(self.work, "branch", "-q", "--set-upstream-to=main")
        self.git(self.work, "switch", "-q", "main")
        self.git(self.work, "switch", "-q", "-c", "local-unmerged", "main")
        self.commit(self.work, "local-unmerged")
        self.git(self.work, "branch", "-q", "--set-upstream-to=main")
        self.git(self.work, "switch", "-q", "main")
        self.git(self.work, "branch", "-q", "-D", "merged-local")

        plain = self.run_prune("--yes")
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        self.assertIn("tracks local main", plain.stdout)
        self.assertEqual(self.branches(), {"main", "local-merged", "local-unmerged"})

        applied = self.run_prune("--yes", "--include-merged")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(self.branches(), {"main", "local-unmerged"})

    def test_include_merged_requires_default_ref_to_match_the_remote(self):
        base = self.git(self.work, "rev-parse", "main")
        self.pushed_branch("feature")
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "merge", "-q", "--no-edit", "origin/feature")
        self.git(self.other, "push", "-q", "origin", "main")
        self.git(self.work, "fetch", "-q")
        # The merge is undone on the remote, and origin/main is no longer fetched.
        self.git(self.other, "push", "-q", "-f", "origin", base + ":refs/heads/main", base + ":refs/heads/feature")
        self.git(self.work, "config", "remote.origin.fetch", "+refs/heads/feature:refs/remotes/origin/feature")

        result = self.run_prune("--yes", "--include-merged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("origin/main does not match the remote", result.stdout)
        self.assertIn("feature", self.branches())

    def test_pushed_but_unmerged_branch_is_kept_for_review(self):
        self.pushed_branch("abandoned")
        self.pushed_branch("moved")
        self.run_prune("--dry-run")
        self.delete_on_remote("abandoned", merge=False)

        result = self.run_prune("--yes")
        self.assertIn("was pushed to origin/abandoned, but is not merged", result.stdout)
        self.assertIn("abandoned", self.branches())

        # After repointing the remote, the snapshot no longer describes it.
        moved_to = self.root / "moved.git"
        self.git(self.root, "init", "--bare", "-q", str(moved_to))
        self.git(self.work, "push", "-q", str(moved_to), "main")
        self.git(self.work, "remote", "set-url", "origin", str(moved_to))
        result = self.run_prune("--yes")
        self.assertNotIn("was pushed to origin/moved", result.stdout)
        self.assertIn("moved", self.branches())

    def test_hidden_index_edits_block_worktree_removal(self):
        self.git(self.work, "switch", "-q", "-c", "flagged", "main")
        self.commit(self.work, "config.ini", "original")
        self.git(self.work, "push", "-q", "-u", "origin", "flagged")
        self.git(self.work, "switch", "-q", "main")
        location = self.root / "elsewhere" / "flagged"
        self.git(self.work, "worktree", "add", "-q", str(location), "flagged")
        self.git(location, "update-index", "--assume-unchanged", "config.ini")
        (location / "config.ini").write_text("local edit")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("flagged")

        result = self.run_prune("--yes")
        self.assertIn("assume-unchanged or skip-worktree", result.stdout)
        self.assertEqual((location / "config.ini").read_text(), "local edit")
        self.assertIn("flagged", self.branches())

    def test_apply_refuses_worktree_that_changed_after_planning(self):
        sha = self.pushed_branch("planned")
        location = self.root / "elsewhere" / "planned"
        self.git(self.work, "worktree", "add", "-q", str(location), "planned")
        # After planning, the worktree is detached and gets a commit only it references.
        self.git(location, "switch", "-q", "--detach")
        self.commit(location, "detached-work")
        script = (
            'source "$1/scripts/lib/clean-targets.sh"; source "$1/scripts/lib/stale-branches.sh"; '
            'SB_GIT_DIR=$2; SB_TMP=$3; sb_apply_delete planned "$4" "$5" test'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", script, "test", str(REPO), str(self.work / ".git"),
             str(self.root / "tmp"), sha, str(location)],
            env=self.env, text=True, capture_output=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no longer has planned", result.stdout)
        self.assertTrue((location / "detached-work").exists())
        self.assertIn("planned", self.branches())

    def test_checked_out_branch_is_blocked(self):
        self.pushed_branch("current")
        self.git(self.work, "switch", "-q", "current")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("current")

        result = self.run_prune("--yes")
        self.assertIn("checked out", result.stdout)
        self.assertIn("current", self.branches())

    def test_declining_confirmation_keeps_branches(self):
        self.pushed_branch("merged")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("merged")

        result = self.run_prune(answers="n")
        self.assertIn("Aborted", result.stdout)
        self.assertIn("merged", self.branches())

    def test_fetch_failure_leaves_branches_unverified_and_fails(self):
        self.pushed_branch("merged")
        self.git(self.work, "push", "-q", "origin", "merged:main")
        self.delete_on_remote("merged")
        self.git(self.work, "fetch", "-q", "--prune")
        self.git(self.work, "remote", "set-url", "origin", str(self.root / "missing.git"))

        result = self.run_prune("--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Fetch failed", result.stdout)
        self.assertIn("could not fetch origin", result.stdout)
        self.assertIn("merged", self.branches())

    def test_recursive_scan_skips_repositories_without_remote_and_generated_folders(self):
        self.pushed_branch("merged")
        self.git(self.other, "fetch", "-q")
        self.delete_on_remote("merged")
        self.git(self.work, "worktree", "add", "-q", str(self.root / "dev" / "wt"), "-b", "side")
        local = self.root / "dev" / "local"
        self.git(self.root, "init", "-q", str(local))
        self.commit(local, "x")
        self.git(local, "switch", "-q", "-c", "feature")
        self.git(local, "config", "branch.feature.merge", "refs/heads/feature")
        nested = self.root / "dev" / "app" / "node_modules" / "pkg"
        nested.mkdir(parents=True)
        self.git(nested, "init", "-q")

        result = self.run_prune("--yes", cwd=self.root / "dev")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 repositories", result.stdout)
        self.assertIn("No remote (skipped)", result.stdout)
        self.assertNotIn("merged", self.branches())
        self.assertEqual(self.branches(local), {"main", "feature"})

    def test_invalid_arguments(self):
        for args in (("--no-fetch",), ("--yes", "--dry-run"), ("--depth", "0"), ("--bogus",),
                     ("--apply",), ("--stale-days", "7"), ("a", "b")):
            result = self.run_prune(*args)
            self.assertEqual(result.returncode, 1, args)

    def test_default_confirmation_and_explicit_dry_run(self):
        self.pushed_branch("merged")
        self.delete_on_remote("merged")
        dry = self.run_prune("--dry-run", answers="y\n")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertNotIn("[y/N]", dry.stdout)
        self.assertIn("merged", self.branches())
        closed = self.run_prune()
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertIn("Aborted: no answer.", closed.stdout)
        self.assertIn("merged", self.branches())
        accepted = self.run_prune(answers="y")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn("[y/N]", accepted.stdout)
        self.assertNotIn("merged", self.branches())

    def test_stale_after_uses_duration_not_implicit_days(self):
        self.env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
        self.git(self.work, "switch", "-q", "-c", "old-local")
        self.commit(self.work, "local")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        self.git(self.work, "switch", "-q", "main")
        result = self.run_prune("--dry-run", "--stale-after", "1m")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("never pushed, last commit", result.stdout)
        invalid = self.run_prune("--dry-run", "--stale-after", "90")
        self.assertEqual(invalid.returncode, 2)

    def test_detached_worktree_with_unpushed_commit_is_counted_not_listed(self):
        detached = self.root / "detached-unpushed"
        self.git(self.work, "worktree", "add", "-q", "--detach", str(detached), "main")
        self.commit(detached, "unpushed")
        result = self.run_prune("--dry-run")
        self.assertRegex(result.stdout, r"Not reported +1 detached worktree\(s\) with unpushed commits")
        self.assertNotIn("detached-unpushed", result.stdout)
        listed = self.run_prune("--include-detached", "--dry-run")
        self.assertIn("detached HEAD not confirmed on a remote", listed.stdout)
        self.assertTrue(detached.exists())

    def test_recent_unpushed_branches_are_counted_not_listed(self):
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
        self.env.update(GIT_AUTHOR_DATE=two_days_ago, GIT_COMMITTER_DATE=two_days_ago)
        self.git(self.work, "switch", "-q", "-c", "fresh-local")
        self.commit(self.work, "local")
        del self.env["GIT_AUTHOR_DATE"], self.env["GIT_COMMITTER_DATE"]
        self.git(self.work, "switch", "-q", "main")
        result = self.run_prune("--dry-run")
        self.assertRegex(result.stdout, r"Not reported +1 unpushed branch\(es\)")
        self.assertNotIn("fresh-local", result.stdout)
        self.assertRegex(result.stdout, r"Nothing to report +1 repository")
        listed = self.run_prune("--dry-run", "--stale-after", "1d")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn("fresh-local", listed.stdout)

    def test_audit_repo_reports_candidates_without_changing_anything(self):
        self.pushed_branch("merged")
        self.git(self.work, "push", "-q", "origin", "merged:main")
        self.delete_on_remote("merged")
        self.git(self.work, "fetch", "-q", "--prune")
        refs_before = self.git(self.work, "for-each-ref")

        result = subprocess.run(["/bin/bash", str(REPO / "scripts" / "audit-repo.sh"), "--skip-security"],
                                cwd=self.work, env=self.env, text=True, capture_output=True, timeout=60)
        self.assertIn("Stale Branches & Worktrees", result.stdout)
        self.assertIn("1 branch(es) whose remote branch was deleted can be pruned", result.stdout)
        self.assertIn("merged — already in origin/main", result.stdout.replace("\x1b[2m", "").replace("\x1b[0m", ""))
        self.assertEqual(self.git(self.work, "for-each-ref"), refs_before)


if __name__ == "__main__":
    unittest.main()
