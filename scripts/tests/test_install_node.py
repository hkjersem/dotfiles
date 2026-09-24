"""Run with python3 -B -m unittest discover -s scripts/tests -p 'test_install_node.py'."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "install-node.sh"
FNM_STUB = r'''
import json
import os
from pathlib import Path
import sys

path = Path(os.environ["FNM_TEST_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
with open(os.environ["FNM_TEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[:1] == ["--corepack-enabled"]:
    args = args[1:]

def save():
    path.write_text(json.dumps(state))

def fail(stage):
    if state.get("fail_at") == stage:
        print("mock failure: " + stage, file=sys.stderr)
        sys.exit(1)

def reject():
    print("unexpected fnm call: " + repr(args), file=sys.stderr)
    sys.exit(98)

if args == ["list"]:
    fail("list_after" if state.get("installed_now") else "list_before")
    for version in state["installed"]:
        aliases = " default, lts-latest" if version == state.get("default") else ""
        print("* " + version + aliases)
    print("* system")
elif args[:1] == ["install"]:
    fail("install")
    if state["target"] not in state["installed"]:
        state["installed"].append(state["target"])
    state["installed_now"] = True
    save()
elif args[:1] == ["exec"] and args[1].startswith("--using="):
    using = args[1].split("=", 1)[1]
    command = args[2:]
    if command == ["node", "--version"]:
        fail("resolve")
        if "resolve_output" in state:
            print(state["resolve_output"])
        elif using == "lts-latest":
            print(state["target"])
        elif using in state.get("aliases", {}):
            print(state["aliases"][using])
        else:
            requested = "v" + using.lstrip("v")
            matches = [v for v in state["installed"]
                       if v == requested or v.startswith(requested + ".")]
            if not matches:
                reject()
            print(max(matches, key=lambda v: tuple(map(int, v[1:].split(".")))))
    elif command[:2] == ["npm", "list"] and using in state["installed"]:
        fail("enumerate")
        print(state.get("globals_raw", json.dumps(state["globals"])))
    elif command[:2] == ["npm", "install"] and using in state["installed"]:
        fail("migrate")
        state["migrated_to"] = using
        state["migrated_packages"] = command[command.index("--") + 1:]
        save()
    else:
        reject()
elif args[:1] == ["default"] and len(args) == 2:
    fail("default")
    state["default"] = args[1]
    save()
elif args[:1] == ["uninstall"] and len(args) == 2:
    fail("uninstall")
    state["installed"].remove(args[1])
    save()
else:
    reject()
'''


class InstallNodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="install-node-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fnm = bin_dir / "fnm"
        fnm.write_text("#!" + sys.executable + "\n" + FNM_STUB)
        fnm.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "calls.jsonl"
        self.env = dict(os.environ, HOME=str(self.root),
                        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                        FNM_TEST_STATE=str(self.state_path),
                        FNM_TEST_LOG=str(self.log_path))

    def run_case(self, request="22", **overrides):
        state = {
            "installed": ["v22.9.0"],
            "target": "v22.10.0",
            "default": "v22.9.0",
            "globals": {"dependencies": {
                "npm": {"version": "10.0.0"},
                "example-cli": {"version": "1.2.3"},
                "@example/tool": {"version": "4.5.6"},
            }},
        }
        state.update(overrides)
        self.state_path.write_text(json.dumps(state))
        self.log_path.write_text("")
        command = ["/bin/bash", str(SCRIPT)]
        if request is not None:
            command.append(request)
        result = subprocess.run(command, env=self.env, cwd=self.root,
                                text=True, capture_output=True, timeout=20)
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        return result, json.loads(self.state_path.read_text()), events

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Done:", result.stdout)

    def assert_no_cleanup(self, events):
        self.assertFalse(any(e[0] == "uninstall" for e in events), events)

    def test_failures_preserve_sources_and_default(self):
        for stage in ("list_before", "install", "resolve", "list_after",
                      "enumerate", "migrate", "default"):
            with self.subTest(stage=stage):
                result, state, events = self.run_case(request="lts", fail_at=stage)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mock failure: " + stage, result.stderr)
                self.assertNotIn("Done:", result.stdout)
                self.assertIn("v22.9.0", state["installed"])
                self.assertEqual(state["default"], "v22.9.0")
                self.assert_no_cleanup(events)
                if stage != "default":
                    self.assertFalse(any(e[0] == "default" for e in events))

    def test_exact_already_installed_version_is_kept(self):
        for request in ("22.9.0", "v22.9.0"):
            with self.subTest(request=request):
                result, state, events = self.run_case(
                    request=request, target="v22.9.0",
                    installed=["v22.9.0", "v22.10.0", "v24.1.0"])
                self.assert_success(result)
                self.assertEqual(state["migrated_to"], "v22.9.0")
                self.assertEqual(set(state["installed"]), {"v22.9.0", "v24.1.0"})
                self.assertIn(["exec", "--using=" + request, "node", "--version"], events)

    def test_partial_version_uses_fnm_resolution(self):
        result, state, _ = self.run_case(
            request="22.9", target="v22.9.1",
            installed=["v22.9.0", "v22.9.1", "v22.10.0"])
        self.assert_success(result)
        self.assertEqual(state["installed"], ["v22.9.1"])
        self.assertEqual(state["migrated_to"], "v22.9.1")

    def test_lts_does_not_pick_newer_non_lts(self):
        result, state, events = self.run_case(
            request="lts", target="v24.1.0",
            installed=["v24.0.0", "v24.1.0", "v25.0.0"])
        self.assert_success(result)
        self.assertEqual(set(state["installed"]), {"v24.1.0", "v25.0.0"})
        self.assertEqual(state["default"], "v24.1.0")
        self.assertEqual(state["migrated_to"], "v24.1.0")
        migration = next(i for i, e in enumerate(events) if e[2:4] == ["npm", "install"])
        default = events.index(["default", "v24.1.0"])
        cleanup = events.index(["uninstall", "v24.0.0"])
        self.assertLess(migration, default)
        self.assertLess(default, cleanup)

    def test_named_alias_is_resolved_by_fnm(self):
        result, state, _ = self.run_case(
            request="lts/example", aliases={"lts/example": "v22.10.0"})
        self.assert_success(result)
        self.assertEqual(state["installed"], ["v22.10.0"])

    def test_scoped_packages_are_pinned_and_npm_is_excluded(self):
        result, state, events = self.run_case()
        self.assert_success(result)
        self.assertEqual(state["migrated_packages"],
                         ["example-cli@1.2.3", "@example/tool@4.5.6"])
        self.assertEqual(state["installed"], ["v22.10.0"])
        self.assertEqual(state["default"], "v22.10.0")
        self.assertIn(["exec", "--using=v22.9.0", "npm", "list",
                       "-g", "--depth", "0", "--json"], events)

    def test_empty_globals_do_not_trigger_an_install(self):
        for listing in ({}, {"dependencies": {}},
                        {"dependencies": {"npm": {"version": "10.0.0"}}}):
            with self.subTest(listing=listing):
                result, state, events = self.run_case(globals=listing)
                self.assert_success(result)
                self.assertEqual(state["installed"], ["v22.10.0"])
                self.assertNotIn("migrated_to", state)
                self.assertFalse(any(e[2:4] == ["npm", "install"] for e in events))

    def test_invalid_global_listings_prevent_cleanup(self):
        for raw in ("", "not-json", "null", "[]", "{}\n{}", '{"error": {}}',
                    '{"dependencies": null}', '{"dependencies": []}',
                    '{"dependencies": {"tool": {}}}',
                    '{"dependencies": {"tool": {"version": 2}}}',
                    '{"dependencies": {"tool": {"version": "1.0.0\\nother"}}}'):
            with self.subTest(raw=raw):
                result, state, events = self.run_case(globals_raw=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("could not parse globals", result.stderr)
                self.assertIn("v22.9.0", state["installed"])
                self.assertNotIn("migrated_to", state)
                self.assert_no_cleanup(events)

    def test_invalid_or_unmanaged_resolution_prevents_cleanup(self):
        for resolved in ("", "system", "v22", "v22.10.0\nextra", "v22.99.0"):
            with self.subTest(resolved=resolved):
                result, _, events = self.run_case(resolve_output=resolved)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_cleanup(events)
                self.assertFalse(any(e[2:4] == ["npm", "list"] for e in events))

    def test_rerun_after_migration_failure_retries_before_cleanup(self):
        result, failed_state, events = self.run_case(fail_at="migrate")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_cleanup(events)
        result, state, events = self.run_case(installed=failed_state["installed"])
        self.assert_success(result)
        self.assertEqual(state["migrated_to"], "v22.10.0")
        self.assertEqual(state["installed"], ["v22.10.0"])
        migration = next(i for i, e in enumerate(events) if e[2:4] == ["npm", "install"])
        self.assertLess(migration, events.index(["uninstall", "v22.9.0"]))

    def test_uninstall_failure_stops_remaining_cleanup(self):
        result, state, events = self.run_case(
            installed=["v22.8.0", "v22.9.0"], fail_at="uninstall")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cleanup stopped", result.stderr)
        self.assertNotIn("Done:", result.stdout)
        self.assertEqual(state["migrated_to"], "v22.10.0")
        self.assertEqual(len([e for e in events if e[0] == "uninstall"]), 1)
        self.assertEqual(set(state["installed"]), {"v22.8.0", "v22.9.0", "v22.10.0"})

    def test_new_major_migrates_but_keeps_previous_majors(self):
        result, state, events = self.run_case(
            request="24", target="v24.1.0", installed=["v20.1.0", "v22.9.0"])
        self.assert_success(result)
        self.assertEqual(state["migrated_to"], "v24.1.0")
        self.assertEqual(set(state["installed"]), {"v20.1.0", "v22.9.0", "v24.1.0"})
        self.assertIn(["exec", "--using=v22.9.0", "npm", "list",
                       "-g", "--depth", "0", "--json"], events)
        self.assert_no_cleanup(events)

    def test_existing_target_does_not_remigrate_unrelated_majors(self):
        result, state, events = self.run_case(installed=["v20.1.0", "v22.10.0"])
        self.assert_success(result)
        self.assertNotIn("migrated_to", state)
        self.assert_no_cleanup(events)

    def test_default_from_another_major_is_unchanged(self):
        result, state, events = self.run_case(
            installed=["v22.9.0", "v24.1.0"], default="v24.1.0")
        self.assert_success(result)
        self.assertEqual(state["default"], "v24.1.0")
        self.assertFalse(any(e[0] == "default" for e in events))

    def test_unset_default_does_not_prevent_migration(self):
        result, state, events = self.run_case(default=None)
        self.assert_success(result)
        self.assertEqual(state["installed"], ["v22.10.0"])
        self.assertFalse(any(e[0] == "default" for e in events))

    def test_first_install_defaults_to_lts_without_migration(self):
        result, state, events = self.run_case(request=None, installed=[])
        self.assert_success(result)
        self.assertEqual(state["installed"], ["v22.10.0"])
        self.assertEqual(state["default"], "v22.10.0")
        self.assertIn(["--corepack-enabled", "install", "--lts"], events)
        self.assertNotIn("migrated_to", state)
        self.assert_no_cleanup(events)


if __name__ == "__main__":
    unittest.main()
