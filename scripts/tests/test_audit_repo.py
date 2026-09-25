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
        for name in ("git", "npm", "pnpm", "bun", "yarn"):
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

    def run_audit(self, *args, cwd=None):
        self.state_file.write_text(json.dumps(self.state))
        self.log.write_text("")
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
        self.assertIn("test script is a placeholder", result.stdout)

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
