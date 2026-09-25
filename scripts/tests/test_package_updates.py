"""Offline integration tests for the update wrappers on system Bash."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
NOW = 1800000000
MOCK_TOOL = r'''
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["UPDATE_TEST_LOG"], "a") as log:
    log.write(json.dumps([tool, args]) + "\n")
fixture = json.loads(Path(os.environ["UPDATE_TEST_FIXTURE"]).read_text())
if tool == "date" and args == ["+%s"]:
    print(fixture["now"])
elif args and args[0] == "outdated":
    if tool == "bun":
        print("| Package | Current | Update | Latest |")
        for package, value in fixture["outdated"].items():
            current = value.get("current", value.get("wanted"))
            print(f"| {package} | {current} | {value['latest']} | {value['latest']} |")
    else:
        print(json.dumps(fixture["outdated"]))
elif tool == "npm" and len(args) >= 3 and args[0] == "view":
    package, field = args[1:3]
    if field == "version" and "@^" in package:
        print(json.dumps(fixture["prefix_versions"][package]))
    else:
        value = fixture["registry"][package][field]
        print(json.dumps(value) if "--json" in args else value)
elif args == ["install"]:
    if fixture.get("fail_install"):
        sys.exit("Simulated install failure")
else:
    sys.exit("Unexpected tool call: " + tool + " " + " ".join(args))
'''


class PackageUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="package-update-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.fixture_path = self.root / "fixture.json"
        for tool in ("npm", "pnpm", "bun", "date"):
            path = self.bin / tool
            path.write_text("#!" + sys.executable + "\n" + MOCK_TOOL)
            path.chmod(0o755)
        (self.bin / "bash").symlink_to("/bin/bash")
        self.env = dict(os.environ)
        for key in ("BASH_ENV", "ENV", "COOLDOWN_MINUTES", "COOLDOWN_SOURCE",
                    "CONFIG_RELEASE_AGE", "AUGMENT_ALWAYS"):
            self.env.pop(key, None)
        self.env.update(
            HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            UPDATE_TEST_LOG=str(self.log), UPDATE_TEST_FIXTURE=str(self.fixture_path),
            COREPACK_ENABLE_NETWORK="0",
        )
        self.fixture = {"now": NOW, "outdated": {}, "registry": {}, "prefix_versions": {}}

    def manifest(self, pm, dependencies, **extra):
        data = {"name": "fixture", "packageManager": pm + "@1.0.0",
                "dependencies": dependencies}
        data.update(extra)
        (self.project / "package.json").write_text(json.dumps(data, indent=2) + "\n")

    def package(self, name, latest="1.2.0", current="1.0.0", ages=None):
        self.fixture["outdated"][name] = {"current": current, "latest": latest}
        times = {}
        for version, age in (ages or {}).items():
            times[version] = datetime.fromtimestamp(
                NOW - age * 60, timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        self.fixture["registry"][name] = {
            "version": latest, "versions": [current, latest], "time": times,
        }

    def invoke(self, *args):
        if self.log.exists():
            self.log.unlink()
        self.fixture_path.write_text(json.dumps(self.fixture))
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts/package-manager/run.sh"), *args],
            cwd=self.project, env=self.env, input="", text=True,
            capture_output=True, timeout=30,
        )

    def calls(self, tool=None):
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row for row in rows if tool is None or row[0] == tool]

    def dependencies(self):
        return json.loads((self.project / "package.json").read_text())["dependencies"]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("unbound variable", result.stderr)
        self.assertNotIn("invalid option", result.stderr)

    def test_dry_run_with_scoped_package_on_all_managers(self):
        self.package("@scope/pkg")
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"@scope/pkg": "^1.0.0"})
                before = (self.project / "package.json").read_bytes()
                result = self.invoke("--dry-run")
                self.assert_success(result)
                self.assertIn("@scope/pkg", result.stdout)
                self.assertIn("1.2.0", result.stdout)
                self.assertIn("Dry run", result.stdout)
                self.assertEqual((self.project / "package.json").read_bytes(), before)
                self.assertFalse(any(args == ["install"] for _, args in self.calls()))

    def test_empty_plan_on_all_managers(self):
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {})
                result = self.invoke("--yes")
                self.assert_success(result)
                self.assertIn("All packages are up to date", result.stdout)
                self.assertFalse(any(args == ["install"] for _, args in self.calls()))

    def test_augmentation_deduplicates_scoped_names_and_updates_existing_entries(self):
        self.package("@scope/pkg")
        self.package("plain", latest="1.1.0")
        self.fixture["outdated"] = {"@scope/pkg": {"current": "1.0.0", "latest": "1.0.1"}}
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"@scope/pkg": "1.0.0", "plain": "1.0.0"},
                              devDependencies={"@scope/pkg": "1.0.0"})
                result = self.invoke("--force", "--yes")
                self.assert_success(result)
                self.assertEqual(self.dependencies(), {"@scope/pkg": "1.2.0", "plain": "1.1.0"})
                self.assertEqual(self.calls("npm").count(["npm", ["view", "@scope/pkg", "version"]]), 1)
                self.assertEqual(self.calls(pm).count([pm, ["install"]]), 1)

    def test_cooldown_cache_keeps_package_histories_separate(self):
        self.package("@scope/fallback", ages={"1.0.0": 20000, "1.1.0": 5000, "1.2.0": 60})
        self.package("boundary", ages={"1.0.0": 20000, "1.2.0": 4320})
        self.package("too-new", ages={"1.0.0": 20000, "1.2.0": 4319})
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {name: "^1.0.0" for name in self.fixture["outdated"]})
                result = self.invoke("--cooldown", "3", "--yes")
                self.assert_success(result)
                self.assertEqual(self.dependencies(), {
                    "@scope/fallback": "^1.1.0", "boundary": "^1.2.0", "too-new": "^1.0.0",
                })
                for name in self.fixture["outdated"]:
                    self.assertEqual(self.calls("npm").count(["npm", ["view", name, "time", "--json"]]), 1)

    def test_all_updates_blocked_by_cooldown(self):
        self.package("recent", ages={"1.0.0": 20000, "1.2.0": 10})
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"recent": "^1.0.0"})
                result = self.invoke("--cooldown", "3", "--yes")
                self.assert_success(result)
                self.assertIn("Nothing to update", result.stdout)
                self.assertEqual(self.dependencies(), {"recent": "^1.0.0"})
                self.assertFalse(any(args == ["install"] for _, args in self.calls()))

    def test_cooldown_exclusions_skip_history_lookup(self):
        self.package("@scope/excluded", ages={"1.2.0": 10})
        self.manifest("pnpm", {"@scope/excluded": "^1.0.0"})
        (self.project / "pnpm-workspace.yaml").write_text(
            "minimumReleaseAge: 4320\nminimumReleaseAgeExclude:\n  - '@scope/*'\n"
        )
        result = self.invoke("--yes")
        self.assert_success(result)
        self.assertEqual(self.dependencies(), {"@scope/excluded": "^1.2.0"})
        self.assertFalse(any("time" in args for _, args in self.calls("npm")))

    def test_major_only_plan_and_explicit_major_opt_in(self):
        self.package("pkg", latest="2.0.0")
        self.fixture["prefix_versions"]["pkg@^1"] = "1.0.0"
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"pkg": "^1.0.0"})
                result = self.invoke("--yes")
                self.assert_success(result)
                self.assertIn("Skipped:", result.stdout)
                self.assertEqual(self.dependencies(), {"pkg": "^1.0.0"})
                result = self.invoke("--major", "--yes")
                self.assert_success(result)
                self.assertEqual(self.dependencies(), {"pkg": "^2.0.0"})

    def test_stable_packages_do_not_move_to_prerelease_latest(self):
        self.package("pkg", latest="1.3.0-beta.1")
        self.fixture["registry"]["pkg"]["versions"] = ["1.0.0", "1.2.0", "1.3.0-beta.1"]
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"pkg": "^1.0.0"})
                result = self.invoke("--yes")
                self.assert_success(result)
                self.assertEqual(self.dependencies(), {"pkg": "^1.2.0"})

    def test_pnpm_catalog_override_and_member_pins_stay_in_sync(self):
        self.package("@scope/pkg")
        self.manifest("pnpm", {"@scope/pkg": "catalog:"})
        (self.project / "pnpm-workspace.yaml").write_text(
            "packages:\n  - 'packages/*'\ncatalog:\n  '@scope/pkg': '1.0.0'\n"
            "overrides:\n  '@scope/pkg': '1.0.0'\n"
        )
        member = self.project / "packages/member"
        member.mkdir(parents=True)
        (member / "package.json").write_text(json.dumps(
            {"dependencies": {"@scope/pkg": "~1.0.0"}}, indent=2,
        ) + "\n")
        result = self.invoke("--force", "--yes")
        self.assert_success(result)
        workspace = (self.project / "pnpm-workspace.yaml").read_text()
        self.assertEqual(workspace.count("'@scope/pkg': '1.2.0'"), 2)
        self.assertEqual(self.dependencies(), {"@scope/pkg": "catalog:"})
        self.assertEqual(json.loads((member / "package.json").read_text())["dependencies"],
                         {"@scope/pkg": "~1.2.0"})
        self.assertEqual(self.calls("npm").count(["npm", ["view", "@scope/pkg", "version"]]), 1)

    def test_install_failure_is_not_reported_as_success(self):
        self.package("pkg")
        self.fixture["fail_install"] = True
        for pm in ("npm", "pnpm", "bun"):
            with self.subTest(pm=pm):
                self.manifest(pm, {"pkg": "^1.0.0"})
                result = self.invoke("--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Simulated install failure", result.stderr)
                self.assertNotIn("Done!", result.stdout)


if __name__ == "__main__":
    unittest.main()
