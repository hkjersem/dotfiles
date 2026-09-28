"""Run with python3 -B -m unittest discover -s scripts/tests -p 'test_corepack_defaults.py'."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "corepack-defaults.sh"
LIB = ROOT / "lib" / "corepack-defaults.sh"
NODE = shutil.which("node")

NPM_STUB = """#!/bin/bash
echo "npm $*" >> "$TEST_LOG"
[[ "$1" == view ]] || exit 97
file="$TEST_REGISTRY/$(printf '%s' "$2" | tr '/@' '__').json"
[[ -f "$file" ]] || exit 1
cat "$file"
"""

COREPACK_STUB = """#!/bin/bash
echo "corepack $*" >> "$TEST_LOG"
[[ -n "$COREPACK_FAIL" ]] && exit 1
exit 0
"""


def iso(days_ago):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def metadata(latest, releases):
    time = {"created": iso(4000), "modified": iso(0)}
    time.update({version: iso(age) for version, age in releases.items()})
    return {"time": time, "dist-tags": {"latest": latest}, "versions": list(releases)}


@unittest.skipIf(NODE is None, "node is required")
class CorepackDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.corepack_home = self.tmp / "corepack"
        self.registry = self.tmp / "registry"
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "log"
        for path in (self.home, self.corepack_home, self.registry, self.bin):
            path.mkdir()
        self.log.write_text("")
        for name, body in (("npm", NPM_STUB), ("corepack", COREPACK_STUB)):
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(0o755)
        os.symlink(NODE, self.bin / "node")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "COREPACK_HOME": str(self.corepack_home),
            "TEST_LOG": str(self.log),
            "TEST_REGISTRY": str(self.registry),
        }

    def set_defaults(self, defaults):
        (self.corepack_home / "lastKnownGood.json").write_text(json.dumps(defaults))

    def publish(self, package, data, wrap=False):
        name = package.replace("/", "_").replace("@", "_")
        (self.registry / f"{name}.json").write_text(json.dumps([data] if wrap else data))

    def run_script(self, **env):
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)], env={**self.env, **env},
            capture_output=True, text=True,
        )

    def installs(self):
        return [line for line in self.log.read_text().splitlines()
                if line.startswith("corepack install")]

    def plan(self, pm):
        result = subprocess.run(
            ["/bin/bash", "-c",
             f'source "{LIB}" && corepack_default_plan {pm}; '
             'rc=$?; echo "$rc|$CP_CURRENT|$CP_TARGET|$CP_MAJOR"'],
            env=self.env, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip().split("|")

    def standard_registry(self, wrap=False):
        self.publish("pnpm", metadata("12.1.0", {
            "10.33.4": 60, "10.34.0": 20, "10.34.5": 1,  # 10.34.5 is inside the cooldown
            "11.2.0": 30, "12.0.0": 10, "12.1.0": 5,
            "12.2.0-rc.0": 4, "13.0.0": 30,           # prerelease and above latest
        }), wrap=wrap)
        self.publish("yarn", metadata("1.22.22", {"1.22.19": 900, "1.22.22": 600}), wrap=wrap)

    def test_updates_within_major_and_reports_new_major(self):
        self.set_defaults({"pnpm": "10.33.4+sha512.abc", "yarn": "1.22.22+sha512.def"})
        self.standard_registry()

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installs(), ["corepack install -g pnpm@10.34.0"])
        self.assertIn("pnpm default 10.33.4 → 10.34.0", result.stdout)
        self.assertIn("pnpm is 2 majors behind 12.1.0", result.stderr)
        self.assertIn("corepack install -g pnpm@12.1.0", result.stderr)
        self.assertIn("yarn default 1.22.22 is current", result.stdout)

    def test_accepts_npm_12_array_output(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry(wrap=True)

        self.assertEqual(self.plan("pnpm"), ["0", "10.33.4", "10.34.0", "12.1.0"])

    def test_npmrc_cooldown_is_respected(self):
        self.set_defaults({"pnpm": "10.33.4"})
        self.standard_registry()
        (self.home / ".npmrc").write_text("min-release-age=30\n")

        self.assertEqual(self.plan("pnpm"), ["0", "10.33.4", "", "11.2.0"])

    def test_missing_default_installs_newest_eligible_release(self):
        self.standard_registry()

        self.assertEqual(self.plan("pnpm"), ["0", "", "12.1.0", ""])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("corepack install -g pnpm@12.1.0", self.installs())

    def test_modern_yarn_uses_the_berry_package(self):
        self.set_defaults({"yarn": "4.1.0"})
        self.publish("@yarnpkg/cli-dist", metadata("4.5.0", {"4.1.0": 90, "4.5.0": 30}))

        self.assertEqual(self.plan("yarn"), ["0", "4.1.0", "4.5.0", ""])

    def test_registry_failure_reports_error_without_installing(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})

        result = self.run_script()

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read pnpm release metadata", result.stderr)
        self.assertEqual(self.installs(), [])

    def test_install_failure_is_reported(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry()

        result = self.run_script(COREPACK_FAIL="1")

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not install pnpm@10.34.0", result.stderr)
        self.assertNotIn("10.33.4 → 10.34.0", result.stdout)

    def add_bun(self, version):
        stub = self.bin / "bun"
        stub.write_text(f"#!/bin/bash\necho {version}\n")
        stub.chmod(0o755)

    def test_reports_new_bun_major_without_installing(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry()
        self.add_bun("1.3.10")
        self.publish("bun", metadata("2.1.0", {"1.3.10": 90, "1.3.12": 20, "2.0.0": 30, "2.1.0": 1}))

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed 1.3.10): 2.0.0 is a new major", result.stdout)
        self.assertFalse(any("bun" in line for line in self.installs()))

    def test_current_bun_major_is_silent_and_missing_bun_is_skipped(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry()
        result = self.run_script()
        self.assertNotIn("bun", result.stdout + result.stderr)
        self.assertFalse(any("view bun" in line for line in self.log.read_text().splitlines()))

        self.add_bun("1.3.10")
        self.publish("bun", metadata("1.3.12", {"1.3.10": 90, "1.3.12": 20}))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("bun", result.stdout + result.stderr)

    def add_fnm_node(self, version, bun=None):
        bin_dir = self.tmp / "fnm" / "node-versions" / version / "installation" / "bin"
        bin_dir.mkdir(parents=True)
        os.symlink(NODE, bin_dir / "node")
        if bun:
            stub = bin_dir / "bun"
            stub.write_text(f"#!/bin/bash\necho {bun}\n")
            stub.chmod(0o755)
        self.env["FNM_DIR"] = str(self.tmp / "fnm")

    def test_bun_is_checked_in_every_fnm_node_version(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry()
        self.add_fnm_node("v22.1.0", bun="1.3.10")
        self.add_fnm_node("v24.1.0")
        self.add_fnm_node("v26.1.0", bun="3.0.0")
        self.publish("bun", metadata("3.0.0", {"1.3.10": 90, "2.0.0": 60, "3.0.0": 30}))

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bun (Node v22.1.0, installed 1.3.10) is 2 majors behind 3.0.0", result.stderr)
        self.assertNotIn("v24.1.0", result.stdout + result.stderr)
        self.assertNotIn("v26.1.0", result.stdout + result.stderr)
        views = [l for l in self.log.read_text().splitlines() if l.startswith("npm view bun")]
        self.assertEqual(len(views), 1)

    def test_one_major_behind_is_informational(self):
        self.set_defaults({"pnpm": "11.2.0", "yarn": "1.22.22"})
        self.standard_registry()

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("corepack: pnpm: 12.1.0 is a new major", result.stdout)
        self.assertNotIn("behind", result.stderr)

    def test_default_inside_cooldown_is_flagged_and_kept(self):
        self.set_defaults({"pnpm": "12.1.0", "yarn": "1.22.22"})
        self.publish("pnpm", metadata("12.1.0", {"12.0.0": 30, "12.1.0": 1}))
        self.publish("yarn", metadata("1.22.22", {"1.22.22": 600}))

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pnpm default 12.1.0 was published 24h ago", result.stderr)
        self.assertEqual(self.installs(), [])

    def test_corepack_versions_are_checked_per_fnm_node(self):
        self.publish("corepack", metadata("0.36.0", {"0.34.6": 200, "0.36.0": 30, "0.37.0": 1}))
        for version, corepack in (("v22.1.0", "0.34.6"), ("v24.1.0", "0.36.0")):
            self.add_fnm_node(version)
            inst = self.tmp / "fnm" / "node-versions" / version / "installation"
            script = inst / "lib" / "corepack.js"
            script.parent.mkdir()
            script.write_text(f"console.log('{corepack}')\n")
            os.symlink(script, inst / "bin" / "corepack")
        self.add_fnm_node("v26.1.0")

        result = subprocess.run(
            ["/bin/bash", "-c",
             f'source "{LIB}"; while IFS= read -r inst; do '
             'label=$(fnm_installation_label "$inst"); '
             'if ver=$(corepack_version_for "$inst"); then '
             'corepack_update_plan "$ver"; echo "$label $ver ${COREPACK_LATEST:-current}"; '
             'else echo "$label missing"; fi; done < <(fnm_node_installations)'],
            env=self.env, capture_output=True, text=True, check=True,
        )

        self.assertEqual(result.stdout.splitlines(), [
            "v22.1.0 0.34.6 0.36.0",
            "v24.1.0 0.36.0 current",
            "v26.1.0 missing",
        ])

    def test_bun_registry_failure_only_warns(self):
        self.set_defaults({"pnpm": "10.33.4", "yarn": "1.22.22"})
        self.standard_registry()
        self.add_bun("1.3.10")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not check bun releases", result.stderr)

    def test_skips_when_corepack_is_missing(self):
        (self.bin / "corepack").unlink()

        result = self.run_script()

        self.assertEqual(result.returncode, 0)
        self.assertIn("corepack: not available", result.stdout)

    def test_bun_is_checked_without_corepack(self):
        (self.bin / "corepack").unlink()
        self.add_bun("1.3.10")
        self.publish("bun", metadata("2.0.0", {"1.3.10": 90, "2.0.0": 30}))

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed 1.3.10): 2.0.0 is a new major", result.stdout)


if __name__ == "__main__":
    unittest.main()
