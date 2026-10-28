"""Offline repository-audit checks with synthetic history and package managers."""

import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MOCK_TOOL = r'''
import json
import os
from pathlib import Path
import subprocess
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
state = json.loads(Path(os.environ["AUDIT_STATE"]).read_text())
with open(os.environ["AUDIT_LOG"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name == "git":
    if args and args[0] == state.get("git_failure"):
        sys.exit("Simulated Git read failure")
    if args[:1] == ["log"]:
        if "--name-only" in args:
            print("\n".join(state.get("changed_files", [])))
        elif any(arg.startswith("--date=format:") for arg in args):
            print("\n".join(state.get("months", [])))
        elif any(arg.startswith("--grep=") for arg in args):
            key = "fire_commits" if any("hotfix" in arg for arg in args) else "bug_commits"
            print("\n".join(state.get(key, [])))
        else:
            print("\n".join(state.get("commits", [])))
    elif args[:1] == ["shortlog"]:
        print(state.get("authors", ""))
    elif "--verify" in args and "HEAD" in args:
        sys.exit(0 if state.get("commits") else 1)
    else:
        sys.exit(subprocess.call([os.environ["AUDIT_REAL_GIT"], *args]))
else:
    operation = args[0] if args else ""
    result = state.get(name + "_" + operation, {})
    print(result.get("stdout", ""))
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr)
    sys.exit(result.get("status", 0))
'''


class AuditRepoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="audit-repo-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".dotfiles").symlink_to(REPO)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state_file = self.root / "state.json"
        self.log = self.root / "calls.jsonl"
        self.state = {
            "commits": ["abc feature", "def feature"],
            "authors": "1 First\n1 Second",
            "npm_audit": {"stdout": json.dumps({"metadata": {"vulnerabilities": {
                "critical": 0, "high": 0, "moderate": 0, "low": 0,
            }}})},
            "npm_outdated": {"stdout": "{}"},
            "pnpm_audit": {"stdout": json.dumps({"metadata": {"vulnerabilities": {
                "critical": 0, "high": 0, "moderate": 0, "low": 0,
            }}})},
            "pnpm_outdated": {"stdout": "{}"},
            "pnpm_config": {"stdout": "4320"},
        }
        self.git = shutil.which("git")
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        AUDIT_STATE=str(self.state_file), AUDIT_LOG=str(self.log),
                        AUDIT_REAL_GIT=self.git)
        for key in ("BASH_ENV", "ENV", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(key, None)
        subprocess.run([self.git, "init", "--quiet", str(self.project)],
                       env=self.env, check=True, capture_output=True)
        for name in ("git", "npm", "pnpm", "bun", "yarn", "node", "corepack"):
            tool = self.bin / name
            tool.write_text("#!" + sys.executable + "\n" + MOCK_TOOL)
            tool.chmod(0o755)

    def write(self, path, content):
        target = self.project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def package(self, pm="npm"):
        self.write("package.json", json.dumps({
            "name": "fixture", "packageManager": pm + "@1.0.0",
            "scripts": {"test": "vitest", "build": "tsc"},
            "devDependencies": {"vitest": "1.0.0"},
        }))
        self.write({"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml",
                    "bun": "bun.lock", "yarn": "yarn.lock"}[pm], "{}")
        self.write("test_example.py", "pass\n")

    def add_remote(self):
        subprocess.run([self.git, "-C", str(self.project), "remote", "add", "origin", "https://example.invalid/x.git"],
                       env=self.env, check=True, capture_output=True)

    def run_audit(self, *args, cwd=None, stage=True):
        self.state_file.write_text(json.dumps(self.state))
        self.log.write_text("")
        if stage:
            subprocess.run([self.git, "-C", str(self.project), "add", "--all"],
                           env=self.env, check=True, capture_output=True)
        status_args = [self.git, "-C", str(self.project), "status", "--porcelain=v1", "-z"]
        before = subprocess.check_output(status_args, env=self.env)
        result = subprocess.run(
            ["/bin/bash", str(REPO / "scripts/audit-repo.sh"), *args],
            cwd=cwd or self.project, env=self.env, text=True, input="",
            capture_output=True, timeout=30,
        )
        result.stdout = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertEqual(subprocess.check_output(status_args, env=self.env), before)
        return result

    def calls(self, name):
        return [args for tool, args in map(json.loads, self.log.read_text().splitlines())
                if tool == name]

    def test_workspace_listing_hidden_without_additional_worktrees(self):
        result = self.run_audit("--no-package-manager")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("─ Workspaces", result.stdout)
        self.assertNotIn("Run audit-repo --workspaces", result.stdout)

    def test_workspace_report_reuses_registered_worktrees_and_keeps_changes(self):
        self.write("source.txt", "source\n")
        git = [self.git, "-C", str(self.project)]
        subprocess.run([*git, "add", "."], env=self.env, check=True, capture_output=True)
        subprocess.run([*git, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], env=self.env, check=True, capture_output=True)
        worktree = self.root / "linked workspace"
        subprocess.run([*git, "worktree", "add", "-qb", "experiment", str(worktree)],
                       env=self.env, check=True, capture_output=True)
        (worktree / "uncommitted.txt").write_text("keep\n")
        (worktree / "node_modules").mkdir()
        (worktree / "node_modules/blob").write_bytes(b"x" * 100_000)
        baseline = self.run_audit("--no-package-manager", stage=False)
        self.assertNotIn("Workspace disk usage", baseline.stdout)
        self.assertIn("─ Workspaces", baseline.stdout)
        self.assertIn("linked\\ workspace", baseline.stdout)
        self.assertIn("experiment", baseline.stdout)
        self.assertIn("Run audit-repo --workspaces for sizes and detailed assessments.", baseline.stdout)
        self.assertFalse(any(args[:2] == ["-C", str(worktree)]
                             for args in self.calls("git")))
        result = self.run_audit("--no-package-manager", "--workspaces", stage=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Workspace disk usage", result.stdout)
        self.assertIn(str(worktree), result.stdout)
        self.assertIn("experiment", result.stdout)
        self.assertIn("uncommitted change(s)", result.stdout)
        self.assertIn("Keep: audited worktree", result.stdout)
        allocated = sum((worktree / name).stat().st_blocks * 512
                        for name in ("source.txt", "uncommitted.txt", "node_modules/blob"))
        self.assertIn(f"{allocated / 1024:.1f} KiB", result.stdout)
        usage = result.stdout.split("─ Workspace disk usage", 1)[1]
        self.assertLess(usage.index(str(worktree)), usage.index(str(self.project)))
        self.assertEqual((worktree / "uncommitted.txt").read_text(), "keep\n")
        self.assertEqual((worktree / "node_modules/blob").stat().st_size, 100_000)
        self.assertFalse(any("fetch" in args for args in self.calls("git")))
        (worktree / "uncommitted.txt").unlink()
        (worktree / "node_modules/blob").unlink()
        (worktree / "node_modules").rmdir()
        self.add_remote()
        for args in (["update-ref", "refs/remotes/origin/master", "HEAD"],
                     ["config", "branch.experiment.remote", "origin"],
                     ["config", "branch.experiment.merge", "refs/heads/experiment"]):
            subprocess.run([*git, *args], env=self.env, check=True, capture_output=True)
        remote = self.run_audit("--workspaces", "--skip-security", stage=False)
        self.assertEqual(remote.returncode, 0, remote.stdout + remote.stderr)
        workspace_section = remote.stdout.split("─ Workspace disk usage", 1)[1]
        self.assertIn("Review: prune_branches candidate;", workspace_section)
        self.assertIn("origin/master", workspace_section)
        self.assertFalse(any("fetch" in args for args in self.calls("git")))

    def test_workspace_report_flags_locked_detached_missing_and_ignored(self):
        self.write(".gitignore", "private/\n")
        git = [self.git, "-C", str(self.project)]
        subprocess.run([*git, "add", "."], env=self.env, check=True, capture_output=True)
        subprocess.run([*git, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], env=self.env, check=True, capture_output=True)
        worktree = self.root / "detached\nworkspace"
        subprocess.run([*git, "worktree", "add", "--detach", str(worktree)],
                       env=self.env, check=True, capture_output=True)
        subprocess.run([*git, "worktree", "lock", str(worktree)],
                       env=self.env, check=True, capture_output=True)
        (worktree / "private").mkdir()
        (worktree / "private/value").write_text("local\n")
        result = self.run_audit("--no-package-manager", "--workspaces", stage=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("detached\\nworkspace", result.stdout)
        self.assertIn("(detached)", result.stdout)
        self.assertIn("Keep: locked worktree", result.stdout)
        self.assertIn("ignored local files: private/", result.stdout)
        self.assertIn("repository has no remote", result.stdout)
        worktree.rename(self.root / "moved")
        missing = self.run_audit("--no-package-manager", "--workspaces", stage=False)
        self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
        self.assertIn("Unknown", missing.stdout)
        self.assertIn("registered directory is missing", missing.stdout)

    def test_no_package_manager_skips_execution_but_keeps_local_checks(self):
        for pm in ("npm", "pnpm", "bun", "yarn"):
            with self.subTest(pm=pm):
                self.package(pm)
                manifest = json.loads((self.project / "package.json").read_text())
                manifest["packageManager"] = pm + "@https://example.invalid/manager.tgz"
                manifest["engines"] = {"node": ">=24"}
                self.write("package.json", json.dumps(manifest))
                result = self.run_audit("--no-package-manager")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"Package manager: {pm}", result.stdout)
                self.assertIn("Security audit skipped (--no-package-manager)", result.stdout)
                self.assertIn("Outdated check skipped (--no-package-manager)", result.stdout)
                self.assertIn("engines.node: >=24", result.stdout)
                self.assertIn("build script: tsc", result.stdout)
                for tool in ("npm", "pnpm", "bun", "yarn", "node", "corepack"):
                    self.assertEqual(self.calls(tool), [], f"{tool} ran:\n{result.stdout}")
                if pm == "pnpm":
                    self.assertIn("pnpm global cooldown skipped (--no-package-manager)", result.stdout)
                self.assertNotIn("No known vulnerabilities", result.stdout)
                self.assertNotIn("All dependencies appear up-to-date", result.stdout)

    def test_no_package_manager_preserves_static_failures_and_cooldowns(self):
        self.package("pnpm")
        self.write("pnpm-workspace.yaml", "minimumReleaseAge: 4320\n")
        manifest = json.loads((self.project / "package.json").read_text())
        manifest["scripts"]["test"] = "exit 1"
        self.write("package.json", json.dumps(manifest))
        result = self.run_audit("--no-package-manager", "--skip-security")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("test script is a placeholder", result.stdout)
        self.assertIn("Release-age cooldown: 3d (pnpm-workspace.yaml)", result.stdout)
        self.assertIn("Summary", result.stdout)
        self.assertEqual(self.calls("pnpm"), [])

    def test_no_package_manager_skips_branch_checks_that_can_execute_git_helpers(self):
        self.add_remote()
        result = self.run_audit("--no-package-manager")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Stale-branch classification skipped (--no-package-manager)", result.stdout)
        for args in self.calls("git"):
            self.assertNotIn("merge-tree", args)
            self.assertNotIn("status", args)
            self.assertNotIn("for-each-ref", args)

    def test_no_package_manager_does_not_lazy_fetch_missing_git_objects(self):
        def git(*args):
            return subprocess.run([self.git, *args], env=self.env, check=True,
                                  capture_output=True, text=True)

        git("-C", str(self.project), "config", "user.name", "Fixture")
        git("-C", str(self.project), "config", "user.email", "fixture@example.invalid")
        self.write("original.txt", "a line of fixture data\n" * 1000)
        git("-C", str(self.project), "add", "--all")
        git("-C", str(self.project), "commit", "--quiet", "-m", "Add fixture")
        git("-C", str(self.project), "mv", "original.txt", "renamed.txt")
        self.write("renamed.txt", "a line of fixture data\n" * 1000 + "changed line\n")
        git("-C", str(self.project), "commit", "--quiet", "--all", "-m", "Rename fixture")
        remote = self.root / "remote.git"
        git("clone", "--quiet", "--bare", str(self.project), str(remote))
        git("--git-dir=" + str(remote), "config", "uploadpack.allowFilter", "true")
        partial = self.root / "partial"
        git("clone", "--quiet", "--filter=blob:none", "--no-checkout", remote.as_uri(), str(partial))
        git("-C", str(partial), "config", "diff.renames", "true")
        objects = partial / ".git/objects"
        before = sorted(path.relative_to(objects).as_posix() for path in objects.rglob("*"))
        trace = self.root / "git-trace"
        env = dict(self.env, GIT_TRACE=str(trace),
                   PATH=str(Path(self.git).parent) + os.pathsep + os.environ["PATH"])
        env.pop("GIT_NO_LAZY_FETCH", None)
        result = subprocess.run(
            ["/bin/bash", str(REPO / "scripts/audit-repo.sh"),
             "--no-package-manager", "--dir", str(partial)],
            env=env, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Could not read Git data", result.stderr)
        self.assertNotRegex(trace.read_text(), r"\bfetch\b")
        self.assertEqual(
            sorted(path.relative_to(objects).as_posix() for path in objects.rglob("*")), before)

    def make_roadmap(self, index="docs/roadmap.md", brief_dir="docs/roadmap"):
        (self.project / index).parent.mkdir(parents=True, exist_ok=True)
        options = ["--root", str(self.project), "--index", index, "--dir", brief_dir]
        script = REPO / "agents/skills/roadmap/scripts/roadmap.py"
        subprocess.run([sys.executable, "-B", str(script), "init", *options],
                       env=self.env, check=True, capture_output=True)
        backlink = os.path.relpath(self.project / index, self.project / brief_dir)
        brief = self.write(f"{brief_dir}/ship.md",
                           "---\ntitle: Ship\nstatus: active\n---\n"
                           f"# Ship\n\n[Roadmap]({backlink})\n")
        subprocess.run([sys.executable, "-B", str(script), "sync", *options],
                       env=self.env, check=True, capture_output=True)
        return self.project / index, brief

    def test_validates_root_and_nested_skill_roadmaps(self):
        self.make_roadmap(index="ROADMAP.md", brief_dir="roadmap")
        self.make_roadmap(index="docs/roadmaps.md")
        (self.project / "docs/roadmap.md").symlink_to("../ROADMAP.md")
        for module in ("pathlib", "roadmaps", "output"):
            self.write(f"{module}.py", "raise AssertionError('repository code was executed')\n")
        result = self.run_audit("--skip-security")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("✓ ROADMAP.md: roadmap layout and briefs are valid", result.stdout)
        self.assertIn("✓ docs/roadmaps.md: roadmap layout and briefs are valid", result.stdout)
        self.assertEqual(result.stdout.count("roadmap layout and briefs are valid"), 2)
        self.assertEqual(result.stderr, "")

    def test_reports_roadmap_validation_errors_without_fixing_them(self):
        index, brief = self.make_roadmap()
        original_index, original_brief = index.read_text(), brief.read_text()
        cases = [
            (original_index, original_brief.replace("status: active", "status: shipped"),
             "status `shipped` is not one of"),
            (original_index, original_brief.replace("status: active",
                                                   "status: active\ndepends_on: [missing]"),
             "depends on unknown item `missing`"),
            (original_index, original_brief.replace("title: Ship", "title: Updated"),
             "generated lists are out of date"),
            (original_index.replace("<!-- roadmap:end -->", ""), original_brief,
             "needs exactly one pair of roadmap markers"),
            (original_index, original_brief + "\n[Missing](missing.md)\n",
             "broken link to missing.md"),
        ]
        for index_text, brief_text, expected in cases:
            with self.subTest(expected=expected):
                index.write_text(index_text)
                brief.write_text(brief_text)
                result = self.run_audit("--skip-security")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertIn("    fix:", result.stdout)
                self.assertNotIn("roadmap layout and briefs are valid", result.stdout)
                self.assertEqual(index.read_text(), index_text)
                self.assertEqual(brief.read_text(), brief_text)

    def test_other_roadmap_formats_are_left_alone(self):
        self.write("docs/roadmap.md", "# Roadmap\n\n- [ ] Ship\n")
        result = self.run_audit("--skip-security")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No skill-managed roadmap found", result.stdout)
        self.assertNotIn("roadmap layout and briefs are valid", result.stdout)

    def test_unreadable_managed_roadmap_fails_validation(self):
        index, brief = self.make_roadmap()
        self.run_audit("--skip-security")
        brief.chmod(0)
        self.addCleanup(brief.chmod, 0o644)
        if os.access(brief, os.R_OK):
            self.skipTest("running with privileges that ignore file permissions")
        result = self.run_audit("--skip-security", stage=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/roadmap.md: cannot read the roadmap", result.stdout)
        self.assertNotIn("roadmap layout and briefs are valid", result.stdout)
        brief.chmod(0o644)
        index.chmod(0)
        self.addCleanup(index.chmod, 0o644)
        result = self.run_audit("--skip-security", stage=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot read (PermissionError)", result.stdout)
        self.assertNotIn("No skill-managed roadmap found", result.stdout)

    def test_roadmap_env_briefs_are_not_read(self):
        self.make_roadmap()
        try:
            self.write("docs/roadmap/.env.local.md", "").write_bytes(b"\xffSECRET")
        except PermissionError:
            self.skipTest("the sandbox denies .env files, so this case is unverified here")
        result = self.run_audit("--skip-security")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("skipped 1 .env roadmap brief(s) without reading them", result.stdout)
        self.assertIn("roadmap validation is incomplete", result.stdout)
        self.assertNotIn("cannot read the roadmap", result.stdout)
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        self.assertNotIn("roadmap layout and briefs are valid", result.stdout)

    def test_unavailable_roadmap_validator_is_not_a_pass(self):
        python = self.bin / "python3"
        python.write_text("#!/bin/sh\necho 'validator unavailable' >&2\nexit 127\n")
        python.chmod(0o755)
        result = self.run_audit("--skip-security")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No skill-managed roadmap found", result.stdout)
        self.assertNotIn("validator unavailable", result.stdout + result.stderr)
        self.make_roadmap()
        result = self.run_audit("--skip-security")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Could not validate skill-managed roadmaps", result.stdout)
        self.assertIn("validator unavailable", result.stdout)
        self.assertNotIn("roadmap layout and briefs are valid", result.stdout)

    def test_helpers_are_relative_to_script_not_home_checkout(self):
        (self.home / ".dotfiles").unlink()
        result = self.run_audit("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_invalid_arguments_and_nonrepository_fail(self):
        for args in (("--unknown",), ("--dir",), ("--since",), ("--dir=",),
                     ("--dir", str(self.home))):
            with self.subTest(args=args):
                result = self.run_audit(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)

    def test_subdirectory_audits_repository_root(self):
        self.package()
        subdir = self.project / "src"
        subdir.mkdir()
        result = self.run_audit("--skip-security", cwd=subdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Package manager: npm", result.stdout)
        self.assertFalse(self.calls("npm") and ["audit", "--json"] in self.calls("npm"))

    def test_empty_signal_array_and_single_file_line_count(self):
        self.state.update(commits=[], authors="")
        self.write("test_example.py", "pass\n")
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("1 tracked files | 1 source files | ~1 lines", result.stdout)
        self.assertIn("Summary", result.stdout)

    def test_unusual_filenames_and_symlinks_are_not_dereferenced(self):
        self.state.update(commits=[], authors="")
        self.write("test odd's\nname.py", "pass\n" * 501)
        self.write("-option.py", "# TODO local\n")
        outside = self.root / "outside.py"
        outside.write_text("TODO external\n" * 900)
        (self.project / "external.py").symlink_to(outside)
        result = self.run_audit()
        self.assertEqual(result.stderr, "")
        self.assertIn("2 tracked files | 2 source files | ~502 lines", result.stdout)
        self.assertIn("1 TODO/FIXME/HACK/XXX", result.stdout)
        self.assertNotIn("900 lines", result.stdout)
        self.assertIn("symlink", result.stdout)
        self.assertEqual(outside.read_text(), "TODO external\n" * 900)

    def test_workspace_label_requires_listed_packages(self):
        self.state.update(commits=[], authors="")
        self.package("pnpm")
        self.write("packages/a/package.json", "{}")
        cases = [
            ("pnpm-workspace.yaml", "packages: []\nminimumReleaseAge: 5760\n", False),
            ("pnpm-workspace.yaml", "minimumReleaseAge: 5760\noverrides:\n  - x\n", False),
            ("pnpm-workspace.yaml", "packages:\n  # comment\n  - 'packages/*'\n", True),
            ("pnpm-workspace.yaml", "packages: ['packages/*']\n", True),
            ("pnpm-workspace.yaml", "packages: # ['packages/*']\nminimumReleaseAge: 5760\n", False),
            ("pnpm-workspace.yaml", "packages: [ # none yet\n]\n", False),
            ("pnpm-workspace.yaml", "packages: [\n  'packages/*',\n]\n", True),
            ("pnpm-workspace.yaml", "packages :\n  - 'packages/*'\n", True),
            ("pnpm-workspace.yaml", '"packages":\r\n  - packages/*\r\n', True),
            ("pnpm-workspace.yaml", "packages:\n---\n", False),
        ]
        for name, content, expected in cases:
            with self.subTest(content=content):
                self.write(name, content)
                result = self.run_audit("--skip-security")
                self.assertEqual("workspace (" in result.stdout, expected, result.stdout[:400])
        (self.project / "pnpm-workspace.yaml").unlink()
        for workspaces, expected in (([], False), (["packages/*"], True), ({"packages": []}, False),
                                     ({"packages": ["packages/*"]}, True), ({"packages": "packages/*"}, False)):
            with self.subTest(workspaces=workspaces):
                manifest = json.loads((self.project / "package.json").read_text())
                manifest["workspaces"] = workspaces
                self.write("package.json", json.dumps(manifest))
                result = self.run_audit("--skip-security")
                self.assertEqual("workspace (" in result.stdout, expected, result.stdout[:400])

    def test_env_files_are_not_read(self):
        self.state.update(commits=[], authors="")
        try:
            self.write(".env.development", "TOKEN=x # TODO\n" * 40)
            self.write("app/.env", "TODO\n")
            self.write("docs/.env.local.md", "[guide](../README.md)\n")
        except PermissionError:
            self.skipTest("the sandbox denies .env files, so this case is unverified here")
        self.write(".environment.ts", "// TODO\n")
        self.write("notes.txt", "TODO\n")
        result = self.run_audit("--skip-security")
        self.assertIn("Skipped 3 tracked .env file(s) without reading them", result.stdout)
        self.assertIn("2 TODO/FIXME/HACK/XXX", result.stdout)
        self.assertIn("~2 lines", result.stdout)
        self.assertIn("Skipped 1 .env Markdown file(s) without reading them", result.stdout)
        self.assertNotIn("link to ../README.md", result.stdout)

    def test_unreadable_files_are_reported_but_not_issues(self):
        self.state.update(commits=[], authors="")
        self.write("notes.txt", "TODO\n")
        self.write("locked.txt", "TODO\nTODO\n")
        self.run_audit("--skip-security")
        locked = self.project / "locked.txt"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        if os.access(locked, os.R_OK):
            self.skipTest("running with privileges that ignore file permissions")
        result = subprocess.run(["/bin/bash", str(REPO / "scripts/audit-repo.sh"), "--skip-security"],
                                cwd=self.project, env=self.env, text=True, input="",
                                capture_output=True, timeout=30)
        out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("Skipped 1 file(s) without read permission", out)
        self.assertNotIn("Could not count lines", out)
        self.assertEqual(result.stderr, "")
        self.assertIn("1 TODO/FIXME/HACK/XXX", out)
        self.assertIn("Conventional file names, placement and Markdown link casing look right", out)
        self.assertEqual(result.returncode, 0, out)

        readme = self.write("README.md", "[notes](NOTES.txt)\n")
        readme.chmod(0)
        self.addCleanup(readme.chmod, 0o644)
        result = subprocess.run(["/bin/bash", str(REPO / "scripts/audit-repo.sh"), "--skip-security"],
                                cwd=self.project, env=self.env, text=True, input="",
                                capture_output=True, timeout=30)
        out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("✗ Could not read 1 documentation file(s)", out)
        self.assertNotIn("Markdown link casing look right", out)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.returncode, 1, out)

        design = self.write("DESIGN.md", "# Design\n")
        design.chmod(0)
        self.addCleanup(design.chmod, 0o644)
        result = subprocess.run(["/bin/bash", str(REPO / "scripts/audit-repo.sh"), "--skip-security"],
                                cwd=self.project, env=self.env, text=True, input="",
                                capture_output=True, timeout=30)
        out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("✗ Could not read 2 documentation file(s)", out)
        self.assertNotIn("has no design tokens", out)
        self.assertEqual(result.stderr, "")

    def test_ci_and_single_author_checks_need_a_remote(self):
        self.state.update(authors="2 Solo")
        self.write("app.py", "pass\n")
        result = self.run_audit("--skip-security")
        self.assertIn("No remote — CI check skipped", result.stdout)
        self.assertNotIn("No CI config found", result.stdout)
        self.assertIn("No remote — knowledge concentration check skipped", result.stdout)
        self.assertNotIn("single-author risk", result.stdout)
        self.add_remote()
        result = self.run_audit("--skip-security")
        self.assertIn("! No CI config found", result.stdout)
        self.assertIn("! Knowledge concentration: Solo wrote 100% of all commits", result.stdout)
        self.write(".github/workflows/ci.yml", "on: push\n")
        result = self.run_audit("--skip-security")
        self.assertIn("GitHub Actions: 1 workflow(s)", result.stdout)

    def test_documentation_names_placement_and_link_casing(self):
        self.state.update(commits=[], authors="")
        self.write("AGENTS.md", "# Rules\n")
        self.write("LICENSE", "MIT\n")
        self.write(".github/copilot-instructions.md", "# Copilot\n")
        self.write("docs/security.md", "# Threat model\n")
        self.write("docs/guide.md", "# Guide\n")
        self.write("DESIGN.md", "---\nname: Brand\ncolors:\n  primary: \"#000\"\n---\n# Design\n")
        result = self.run_audit("--skip-security")
        self.assertIn("Conventional file names, placement and Markdown link casing look right", result.stdout)

        self.write("docs/agents.md", "# Agent API\n")
        self.write("pkg/Readme.md", "# Package\n")
        self.write("changelog.md", "# Changes\n")
        self.write("copilot-instructions.md", "# Misplaced\n")
        self.write("app/DESIGN.md", "# Architecture\n\nNo tokens.\n")
        self.write("README.md", "[ok](docs/guide.md) [bad](docs/Guide.md#top) [gone](docs/missing.md)\n"
                   "[ref]: ./DOCS/guide.md\n`[code](docs/GUIDE.md)`\n```\n[fenced](docs/GUIDE.md)\n```\n"
                   "[web](https://example.com/Docs/Guide.md)\n")
        result = self.run_audit("--skip-security")
        out = result.stdout
        self.assertNotIn("look right", out)
        self.assertIn("docs/agents.md: agents that look for AGENTS.md here", out)
        self.assertIn("pkg/Readme.md: name it README.md", out)
        self.assertIn("changelog.md: name it CHANGELOG.md", out)
        self.assertIn("copilot-instructions.md: Copilot reads only .github/copilot-instructions.md", out)
        self.assertIn("app/DESIGN.md has no design tokens", out)
        self.assertEqual(out.count("has no design tokens"), 1)
        self.assertIn("README.md: link to docs/Guide.md differs in case from docs/guide.md", out)
        self.assertIn("README.md: link to ./DOCS/guide.md differs in case from docs/guide.md", out)
        self.assertNotIn("GUIDE.md", out)
        self.assertNotIn("missing.md", out)
        self.assertNotIn("security.md", out)
        self.assertEqual(out.count("differs in case"), 2)

    def test_audit_failures_and_unknown_shapes_never_report_clean(self):
        self.package()
        cases = [
            {"stdout": '{"error":{"code":"ENETWORK"}}', "status": 1},
            {"stdout": "not json", "status": 1, "stderr": "network unavailable"},
            {"stdout": "{}"},
            {"stdout": '{"metadata":{"vulnerabilities":{"critical":-1,"high":0,"moderate":0,"low":0}}}'},
            {**self.state["npm_audit"], "status": 1},
            {"stdout": "", "status": 127, "stderr": "missing tool"},
        ]
        for output in cases:
            with self.subTest(output=output):
                self.state["npm_audit"] = output
                result = self.run_audit()
                self.assertNotIn("No known vulnerabilities", result.stdout)
                self.assertIn("could not", result.stdout.lower())
                self.assertIn("Summary", result.stdout)
                self.assertEqual(result.returncode, 1, result.stdout)

    def test_vulnerabilities_are_counted_even_when_audit_exits_one(self):
        self.package()
        self.state["npm_audit"] = {"status": 1, "stdout": json.dumps({
            "metadata": {"vulnerabilities": {"critical": 1, "high": 2, "moderate": 3, "low": 4}},
        })}
        result = self.run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Critical vulnerabilities: 1", result.stdout)
        self.assertIn("High vulnerabilities: 2", result.stdout)
        self.assertIn("Moderate vulnerabilities: 3", result.stdout)

    def test_outdated_counts_packages_not_table_lines_and_reports_failure(self):
        for pm in ("npm", "pnpm"):
            self.package(pm)
            for payload, expected in (
                ({"stdout": "{}"}, "All dependencies appear up-to-date"),
                ({"status": 1, "stdout": '{"one":{"latest":"2.0.0"},"two":{"latest":"3.0.0"}}'},
                 "2 outdated package(s)"),
                ({"status": 1, "stdout": '{"error":{"code":"ENETWORK"}}'}, "Outdated check could not"),
                ({"status": 1, "stdout": "", "stderr": "offline"}, "Outdated check could not"),
            ):
                with self.subTest(pm=pm, payload=payload):
                    self.state[pm + "_outdated"] = payload
                    result = self.run_audit("--skip-security")
                    self.assertIn(expected, result.stdout)
                    self.assertEqual(result.returncode, 1 if "could not" in expected else 0, result.stdout)
                    self.assertIn(["outdated", "--json"], self.calls(pm))
                    self.assertNotIn(["audit", "--json"], self.calls(pm))

    def test_bun_and_unsupported_managers_do_not_make_false_support_claims(self):
        for pm in ("bun", "yarn"):
            with self.subTest(pm=pm):
                self.package(pm)
                result = self.run_audit()
                self.assertNotIn("does not support", result.stdout)
                self.assertNotIn("not supported", result.stdout)
                self.assertFalse(self.calls(pm))
                self.assertIn("not implemented", result.stdout)

    def test_global_cooldowns_and_invalid_values(self):
        self.package("npm")
        (self.home / ".npmrc").write_text("min-release-age=3\n")
        result = self.run_audit("--skip-security")
        self.assertIn("Release-age cooldown: 3d", result.stdout)
        self.package("pnpm")
        for value, expected in (("oops", "Unrecognized"), ("0", "disabled"),
                                ("0008", "Release-age cooldown: 8m"),
                                ("4320 # comment", "Release-age cooldown: 3d"),
                                ("$(false)", "Unrecognized")):
            with self.subTest(value=value):
                self.write(".npmrc", "minimum-release-age=" + value + "\n")
                result = self.run_audit("--skip-security")
                self.assertEqual(result.stderr, "")
                self.assertIn(expected, result.stdout)
                self.assertIn("Summary", result.stdout)

    def test_vendored_node_runtime_is_flagged_only_when_it_escapes_the_repository(self):
        self.package("pnpm")
        modules = self.project / "node_modules"
        (modules / ".bin").mkdir(parents=True)
        outside = self.root / "pnpm-store" / "node" / "bin"
        outside.mkdir(parents=True)
        (outside / "node").write_text("#!/bin/sh\n")
        inside = modules / ".pnpm" / "node@runtime" / "bin"
        inside.mkdir(parents=True)
        (inside / "node").write_text("#!/bin/sh\n")

        (modules / ".bin" / "node").symlink_to(outside / "node")
        result = self.run_audit("--skip-security")
        self.assertEqual(result.stderr, "")
        self.assertIn("resolves outside the repository", result.stdout)
        self.assertIn("rm -f node_modules/node", result.stdout)

        (modules / ".bin" / "node").unlink()
        (modules / ".bin" / "node").symlink_to(inside / "node")
        result = self.run_audit("--skip-security")
        self.assertEqual(result.stderr, "")
        self.assertNotIn("resolves outside the repository", result.stdout)
        self.assertIn("kept inside the repository", result.stdout)

        (modules / ".bin" / "node").unlink()
        result = self.run_audit("--skip-security")
        self.assertNotIn("Vendored Node runtime", result.stdout)

    def test_devengines_download_notice_respects_runtime_opt_out(self):
        self.package("pnpm")
        manifest = json.loads((self.project / "package.json").read_text())
        manifest["devEngines"] = {"runtime": {"name": "node", "version": "^24.19.0",
                                              "onFail": "download"}}
        self.write("package.json", json.dumps(manifest))
        result = self.run_audit("--skip-security")
        self.assertEqual(result.stderr, "")
        self.assertIn("onFail=download", result.stdout)
        self.assertIn("runtimeOnFail: ignore", result.stdout)

        for value in ("ignore", '"ignore"', "ignore # keep fnm's node"):
            with self.subTest(value=value):
                self.write("pnpm-workspace.yaml", "runtimeOnFail: " + value + "\n")
                result = self.run_audit("--skip-security")
                self.assertEqual(result.stderr, "")
                self.assertNotIn("onFail=download", result.stdout)

        self.write("pnpm-workspace.yaml", "runtimeOnFail: error\n")
        result = self.run_audit("--skip-security")
        self.assertIn("onFail=download", result.stdout)

    def test_jsonc_and_inherited_typescript_settings_are_not_declared_disabled(self):
        self.package()
        self.write("source.ts", "export {};\n")
        for content in ('{// comment\n"compilerOptions":{"strict":true}}',
                        '{"extends":"./base.json"}'):
            with self.subTest(content=content):
                self.write("tsconfig.json", content)
                result = self.run_audit("--skip-security")
                self.assertNotIn("strict mode not enabled", result.stdout)
                self.assertIn("not evaluated", result.stdout)

    def test_history_uses_consistent_filters_and_requested_lookback(self):
        self.write("test_example.py", "pass\n")
        self.state["changed_files"] = ["packages/app/dist/generated.js", "test_example.py"]
        result = self.run_audit("--since", "2 weeks ago")
        self.assertNotIn("generated.js", result.stdout)
        self.assertNotIn("last year", result.stdout)
        for args in self.calls("git"):
            if args[:1] == ["log"]:
                self.assertIn("--no-merges", args)

    def test_history_accepts_compact_duration(self):
        result = self.run_audit("--since", "7d", "--no-package-manager")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("--since=604800 seconds ago" in args for args in self.calls("git")))

    def test_inactive_months_are_included_in_velocity(self):
        today = datetime.date.today()
        older = today.replace(day=1) - datetime.timedelta(days=180)
        self.state["months"] = [older.strftime("%Y-%m")] * 24
        self.write("test_example.py", "pass\n")
        result = self.run_audit()
        self.assertIn("Velocity: slowing", result.stdout)
        self.assertIn("recent avg 0/mo vs 12-mo avg 2/mo", result.stdout)
        self.assertIn("including inactive months", result.stdout)

    def test_framework_scopes_and_default_test_placeholder(self):
        self.package()
        manifest = json.loads((self.project / "package.json").read_text())
        manifest["devDependencies"] = {"@playwright/test": "1.0.0"}
        manifest["scripts"]["test"] = 'echo "Error: no test specified" && exit 1'
        self.write("package.json", json.dumps(manifest))
        result = self.run_audit("--skip-security")
        self.assertIn("Test framework(s): playwright", result.stdout)
        self.assertIn("✗ test script is a placeholder", result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)
        for script in ('echo "Error: no test specified" && exit 1 || true', 'echo "Error: no test specified"'):
            with self.subTest(script=script):
                manifest["scripts"]["test"] = script
                self.write("package.json", json.dumps(manifest))
                result = self.run_audit("--skip-security")
                self.assertNotIn("placeholder", result.stdout)
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_history_and_test_discovery_heuristics_warn_without_failing(self):
        commits = ["c%d fix" % i for i in range(10)]
        self.state.update(commits=commits, authors="10 Solo", changed_files=["app.py"] * 10,
                          bug_commits=commits, fire_commits=commits[:6])
        self.write("app.py", "pass\n")
        self.add_remote()
        result = self.run_audit("--skip-security")
        for finding in ("Top churn: app.py changed in 100%", "Solo wrote 100% of all commits",
                        "10 fix-labelled commits (100% of total)", "Firefighting: 6 hotfix",
                        "No test files found"):
            self.assertRegex(result.stdout, "! [^\n]*" + re.escape(finding))
        self.assertNotIn("✗ ", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_git_read_failure_is_explicit(self):
        for command in ("log", "shortlog", "ls-files"):
            with self.subTest(command=command):
                self.state["git_failure"] = command
                result = self.run_audit()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Simulated Git read failure", result.stderr)
                self.assertNotIn("No notable signals", result.stdout)

    def test_manager_detection_stays_inside_repository_and_checks_lockfile(self):
        self.package()
        manifest = json.loads((self.project / "package.json").read_text())
        manifest.pop("packageManager")
        self.write("package.json", json.dumps(manifest))
        (self.project / "package-lock.json").unlink()
        (self.root / "package.json").write_text('{"packageManager":"pnpm@11.0.0"}')
        result = self.run_audit("--skip-security")
        self.assertIn("Package manager: npm", result.stdout)
        self.assertFalse(self.calls("pnpm"))
        self.package("pnpm")
        (self.project / "pnpm-lock.yaml").unlink()
        self.write("package-lock.json", "{}")
        result = self.run_audit("--skip-security")
        self.assertIn("packageManager field says 'pnpm' but lockfile belongs to 'npm'", result.stdout)

    def test_generated_files_are_excluded_at_any_depth(self):
        self.state.update(commits=[], authors="")
        self.write("test_example.py", "pass\n")
        self.write("packages/app/dist/generated.js", "TODO\n" * 600)
        self.write("node_modules/dependency/index.js", "TODO\n" * 600)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 tracked files | 1 source files | ~1 lines", result.stdout)
        self.assertIn("No TODO/FIXME/HACK/XXX markers", result.stdout)


if __name__ == "__main__":
    unittest.main()
