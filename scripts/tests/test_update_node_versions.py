"""Run with python3 -B -m unittest discover -s scripts/tests -p 'test_update_node_versions.py'."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

REGISTRY_NPM = """#!/bin/bash
echo "npm $*" >> "$TEST_LOG"
[[ "$1" == view ]] || exit 97
[[ -n "$REGISTRY_DOWN" ]] && exit 1
file="$TEST_REGISTRY/$(printf '%s' "$2" | tr '/@' '__').json"
[[ -f "$file" ]] || exit 1
cat "$file"
"""

# npm inside one fake fnm installation; its version lives next to it.
INSTALLATION_NPM = """#!/bin/bash
inst="$(cd "$(dirname "$0")/.." && pwd)"
label="$(basename "$(dirname "$inst")")"
case "$1" in
  --version) cat "$inst/npm-version" ;;
  install) echo "$label npm $*" >> "$TEST_LOG"; echo "${@: -1}" | sed 's/^npm@//' > "$inst/npm-version" ;;
  update|outdated) echo "$label npm $*" >> "$TEST_LOG" ;;
  *) exit 97 ;;
esac
"""

NPM_10 = "^18.17.0 || >=20.5.0"
NPM_11 = "^20.17.0 || >=22.9.0"
NPM_12 = "^22.22.2 || ^24.15.0 || >=26.0.0"


def iso(days_ago):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@unittest.skipIf(NODE is None, "node is required")
class UpdateNodeVersionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.scripts = self.tmp / "scripts"
        (self.scripts / "lib").mkdir(parents=True)
        shutil.copy(ROOT / "update-node-versions.sh", self.scripts)
        for lib in ("corepack-defaults.sh", "pm-utils.sh"):
            shutil.copy(ROOT / "lib" / lib, self.scripts / "lib")
        (self.scripts / "install-node.sh").write_text(
            '#!/bin/bash\necho "install-node $*" >> "$TEST_LOG"\n')
        self.home = self.tmp / "home"
        self.registry = self.tmp / "registry"
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "log"
        for path in (self.home, self.registry, self.bin):
            path.mkdir()
        self.log.write_text("")
        (self.home / ".npmrc").write_text("min-release-age=3\n")
        for name, body in (("npm", REGISTRY_NPM),
                           ("fnm", '#!/bin/bash\n[[ "$1" == current ]] && echo "$TEST_CURRENT"\n')):
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(0o755)
        os.symlink(NODE, self.bin / "node")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "FNM_DIR": str(self.tmp / "fnm"),
            "TEST_LOG": str(self.log),
            "TEST_REGISTRY": str(self.registry),
            "TEST_CURRENT": "v24.22.0",
        }

    def add_node(self, version, npm):
        inst = self.tmp / "fnm" / "node-versions" / version / "installation"
        (inst / "bin").mkdir(parents=True)
        os.symlink(NODE, inst / "bin" / "node")
        stub = inst / "bin" / "npm"
        stub.write_text(INSTALLATION_NPM)
        stub.chmod(0o755)
        (inst / "npm-version").write_text(npm + "\n")

    def npm_version(self, version):
        path = self.tmp / "fnm" / "node-versions" / version / "installation" / "npm-version"
        return path.read_text().strip()

    def publish_npm(self, releases, latest=None):
        """releases: version -> (age in days, engines.node)."""
        latest = latest or max(releases, key=lambda v: tuple(map(int, v.split("."))))
        time = {"created": iso(4000), "modified": iso(0)}
        time.update({v: iso(age) for v, (age, _) in releases.items()})
        (self.registry / "npm.json").write_text(json.dumps(
            {"time": time, "dist-tags": {"latest": latest}, "versions": list(releases)}))
        (self.registry / "npm_>=10.json").write_text(json.dumps(
            [{"version": v, "engines.node": e} for v, (_, e) in releases.items()]))

    def run_script(self, **env):
        return subprocess.run(
            ["/bin/bash", str(self.scripts / "update-node-versions.sh")],
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def log_lines(self, prefix):
        return [l for l in self.log.read_text().splitlines() if l.startswith(prefix)]

    def test_each_installation_gets_the_newest_npm_its_node_supports(self):
        # Node 22.10 is below npm 12's ^22.22.2, so it stops at npm 11.
        self.add_node("v22.10.0", "10.9.9")
        self.add_node("v22.23.3", "10.9.9")
        self.add_node("v24.22.0", "12.0.0")
        self.publish_npm({"10.9.9": (300, NPM_10), "11.21.1": (100, NPM_11),
                          "12.0.0": (40, NPM_12), "12.2.1": (10, NPM_12),
                          "12.3.0": (1, NPM_12)})  # inside the cooldown

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.npm_version("v22.10.0"), "11.21.1")
        self.assertEqual(self.npm_version("v22.23.3"), "12.2.1")
        self.assertEqual(self.npm_version("v24.22.0"), "12.2.1")
        self.assertEqual(self.log_lines("install-node"), ["install-node 22"])
        self.assertEqual(len(self.log_lines("v22.10.0 npm update -g")), 1)
        self.assertIn("npm 12.2.1 no longer supports Node v22.10.0; it stays on npm 11.21.1",
                      result.stderr)

    def test_versions_above_the_latest_tag_are_not_installed(self):
        # npm tags some patches only as next-12, and `npm update -g` would
        # move them back to `latest` on every run.
        self.add_node("v24.22.0", "12.0.0")
        self.publish_npm({"12.0.0": (40, NPM_12), "12.2.0": (20, NPM_12),
                          "12.2.1": (5, NPM_12)}, latest="12.2.0")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.npm_version("v24.22.0"), "12.2.0")

    def test_global_updates_are_skipped_where_npm_ignores_the_cooldown(self):
        self.add_node("v20.5.0", "10.8.2")
        self.publish_npm({"10.9.9": (300, NPM_10), "11.21.1": (100, NPM_11)})

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.npm_version("v20.5.0"), "10.9.9")
        self.assertEqual(self.log_lines("v20.5.0 npm update"), [])
        self.assertIn("npm 10.9.9 for Node v20.5.0 ignores min-release-age", result.stderr)

    def test_node_older_than_any_npm_10_keeps_its_npm(self):
        self.add_node("v16.20.2", "8.19.4")
        self.publish_npm({"10.9.9": (300, NPM_10), "11.21.1": (100, NPM_11)})

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.npm_version("v16.20.2"), "8.19.4")
        self.assertEqual(self.log_lines("v16.20.2 npm"), [])
        self.assertIn("no cooldown-eligible npm 10 or newer supports Node v16.20.2; "
                      "npm was left at 8.19.4", result.stderr)

    def test_registry_failure_keeps_npm_but_still_updates_safe_globals(self):
        self.add_node("v24.22.0", "12.0.0")
        self.add_node("v22.23.3", "10.9.9")

        result = self.run_script(REGISTRY_DOWN="1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log_lines("v24.22.0 npm install"), [])
        self.assertEqual(len(self.log_lines("v24.22.0 npm update -g")), 1)
        self.assertEqual(self.log_lines("v22.23.3 npm update"), [])
        self.assertIn("could not check npm releases for Node v24.22.0", result.stderr)

    def test_install_node_failure_is_reported_after_the_rest_runs(self):
        self.add_node("v22.23.3", "12.2.1")
        self.add_node("v24.22.0", "12.2.1")
        self.publish_npm({"12.2.1": (10, NPM_12)})
        (self.scripts / "install-node.sh").write_text("#!/bin/bash\nexit 1\n")

        result = self.run_script()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.log_lines("v24.22.0 npm update -g")), 1)


if __name__ == "__main__":
    unittest.main()
