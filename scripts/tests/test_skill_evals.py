"""Tests for skill-evals: isolated workspaces, arm flags and suspicious-run detection, using a fake copilot."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "skill-evals.py"

# As a run, records its arguments and working directory and writes a transcript that shows a
# skill call unless skills are excluded (or FAKE_ALWAYS_SKILL imitates a leaking baseline).
# As a grader (assertions.json present), passes every assertion for runs with the skill and only
# the first without, citing its working folder as evidence. FAKE_BAD_GRADE=count, text or exit
# makes it return too few entries, entries for other assertions, or a valid file and exit 1.
FAKE_COPILOT = """#!/bin/bash
share="" usage="" excluded=0
while [ $# -gt 0 ]; do
  case $1 in
    --share) share=$2; shift ;;
    --usage-output-file) usage=$2; shift ;;
    --excluded-tools) excluded=1; shift ;;
  esac
  shift
done
if [ -f assertions.json ]; then
  printf '{"modelMetrics": {"fake-grader": {}}}' > "$usage"
  python3 -c '
import json, os
assertions = json.load(open("assertions.json"))
with_skill = "excluded=0" in open("outputs/answer.md").read()
bad = os.environ.get("FAKE_BAD_GRADE", "")
entries = [{"text": "x" if bad == "text" else text, "passed": with_skill or index == 0, "evidence": os.getcwd()}
           for index, text in enumerate(assertions)]
json.dump({"expectations": entries[:1] if bad == "count" else entries}, open("grading.json", "w"))'
  [ "${FAKE_BAD_GRADE:-}" = exit ] && exit 1
  exit 0
fi
printf '# run\\n' > "$share"
if [ "$excluded" = 0 ] || [ -n "${FAKE_ALWAYS_SKILL:-}" ]; then printf '\\n### `skill`\\n' >> "$share"; fi
printf '{"tokenDetails": {"output": {"tokenCount": 5}}, "modelMetrics": {"fake-model": {}}}' > "$usage"
printf 'cwd=%s\\nexcluded=%s\\n' "$PWD" "$excluded"
ls -R
"""


class SkillEvalsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skill-evals-test-")
        root = Path(self.temporary.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "copilot"
        fake.write_text(FAKE_COPILOT)
        fake.chmod(0o755)
        self.out = root / "out"
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", NO_COLOR="1")

    def tearDown(self):
        self.temporary.cleanup()

    def run_evals(self, *specs, env=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *specs, "--out", str(self.out)],
                              capture_output=True, text=True, env=env or self.env, timeout=120)

    def answer(self, skill, ident, arm):
        return (self.out / skill / f"eval-{ident}" / arm / "run-1" / "outputs" / "answer.md").read_text()

    def test_only_the_baseline_excludes_skills_and_setup_builds_the_workdir(self):
        result = self.run_evals("dotfiles:3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with_skill = self.answer("dotfiles", 3, "with_skill")
        without_skill = self.answer("dotfiles", 3, "without_skill")
        self.assertIn("excluded=0", with_skill)
        self.assertIn("excluded=1", without_skill)
        workspace = self.out / "dotfiles" / "eval-3" / "with_skill" / "run-1" / "workspace"
        self.assertIn(f"cwd={workspace.resolve() / 'demo'}", with_skill)
        self.assertTrue((workspace / "remote.git").is_dir())
        metadata = json.loads((self.out / "dotfiles" / "eval-3" / "eval_metadata.json").read_text())
        self.assertTrue(metadata["assertions"])

    def test_fixture_files_are_copied_relative_to_their_common_folder(self):
        result = self.run_evals("blast-radius:1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        workspace = self.out / "blast-radius" / "eval-1" / "without_skill" / "run-1" / "workspace"
        self.assertTrue((workspace / "change.diff").is_file())
        self.assertTrue((workspace / "src" / "cache_keys.py").is_file())

    def test_a_baseline_that_uses_a_skill_fails_the_run(self):
        result = self.run_evals("test-plan:3", env=dict(self.env, FAKE_ALWAYS_SKILL="1"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("without_skill 1", result.stdout)
        self.assertIn("skill used", result.stdout)

    def test_grade_writes_blind_gradings_and_compares_the_arms(self):
        result = self.run_evals("test-plan:3", "test-plan:2", "--grade")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        case = self.out / "test-plan" / "eval-2"
        assertions = json.loads((case / "eval_metadata.json").read_text())["assertions"]
        total = len(assertions)
        with_skill = json.loads((case / "with_skill" / "run-1" / "grading.json").read_text())
        without_skill = json.loads((case / "without_skill" / "run-1" / "grading.json").read_text())
        self.assertEqual([entry["text"] for entry in with_skill["expectations"]], assertions)
        self.assertEqual(with_skill["summary"]["passed"], total)
        self.assertEqual(without_skill["summary"]["passed"], 1)
        for grading in (with_skill, without_skill):
            evidence = grading["expectations"][0]["evidence"]
            self.assertNotIn("with_skill", evidence)
            self.assertNotIn("without_skill", evidence)
        gap = round(100 * (total - 1) / total)
        self.assertRegex(result.stdout, rf"test-plan:2 +{total}/{total} 100% +1/{total} \d+% +\+{gap}")
        self.assertIn("test-plan:3 guard", result.stdout)
        self.assertRegex(result.stdout, r"test-plan:2 +assertion 1 does not separate the arms")
        self.assertNotIn("assertion 2 does not", result.stdout)
        self.assertNotRegex(result.stdout, r"test-plan:3 +assertion")
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual(summary["runner_models"], ["fake-model"])
        self.assertEqual(summary["grader_models"], ["fake-grader"])
        self.assertEqual(summary["cases"]["test-plan:2"]["gap"], gap)
        self.assertTrue(summary["cases"]["test-plan:3"]["guard"])

    def test_regrade_rejects_an_invalid_grading(self):
        self.assertEqual(self.run_evals("test-plan:3").returncode, 0)
        for bad, message in (("count", "no valid grading.json"), ("text", "no valid grading.json"),
                             ("exit", "grader exited 1")):
            with self.subTest(bad=bad):
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--regrade", str(self.out)],
                                        capture_output=True, text=True, timeout=120,
                                        env=dict(self.env, FAKE_BAD_GRADE=bad))
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
                self.assertFalse(list(self.out.glob("*/eval-*/*/run-*/grading.json")))

    def test_a_relative_out_folder_holds_the_run_artifacts(self):
        root = self.out.parent
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "test-plan:3", "--out", "relative"],
                                capture_output=True, text=True, env=self.env, timeout=120, cwd=root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = root / "relative" / "test-plan" / "eval-3" / "with_skill" / "run-1"
        self.assertIn("### `skill`", (run / "outputs" / "transcript.md").read_text())

    def test_dry_run_starts_no_agent(self):
        result = self.run_evals("test-plan:3", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("Dry run"))
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
