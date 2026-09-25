"""Skills synchronization and repair tests using disposable homes and fake npx."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MOCK_NPX = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["SKILLS_TEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if os.environ.get("SKILLS_TEST_FAIL"):
    sys.exit("Simulated skills command failure")
if os.environ.get("SKILLS_TEST_NOOP"):
    sys.exit(0)
sys.stdin.read()
home = Path(os.environ["HOME"])
canonical = home / ".agents/skills"
lock = home / ".agents/.skill-lock.json"
state = json.loads(lock.read_text()) if lock.exists() else {"skills": {}}
if args[2] == "add":
    name = args[args.index("--skill") + 1]
    target = canonical / name
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("fixture skill\n")
    state["skills"][name] = {"source": args[3]}
elif args[2] == "remove":
    name = args[3]
    (canonical / name / "SKILL.md").unlink()
    (canonical / name).rmdir()
    state["skills"].pop(name, None)
lock.write_text(json.dumps(state))
'''


class AgentSkillsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agent-skills-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.dotfiles = self.home / ".dotfiles"
        self.authored = self.dotfiles / "agents/skills"
        self.authored.mkdir(parents=True)
        (self.dotfiles / "scripts").mkdir()
        (self.dotfiles / "scripts/lib").symlink_to(REPO / "scripts/lib")
        self.manifest = self.authored / "skills.txt"
        self.manifest.write_text("")
        self.canonical = self.home / ".agents/skills"
        self.canonical.mkdir(parents=True)
        self.lock = self.home / ".agents/.skill-lock.json"
        self.log = self.root / "npx.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        npx = self.bin / "npx"
        npx.write_text("#!" + sys.executable + "\n" + MOCK_NPX)
        npx.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), DOTFILES=str(self.dotfiles),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        SKILLS_TEST_LOG=str(self.log))
        for key in ("BASH_ENV", "ENV", "SKILLS_TEST_FAIL", "SKILLS_TEST_NOOP"):
            self.env.pop(key, None)

    def run_script(self, name, *args):
        return subprocess.run(
            ["/bin/bash", str(REPO / "scripts" / name), *args],
            cwd=self.root, env=self.env, input="", text=True,
            capture_output=True, timeout=20,
        )

    def snapshot(self):
        result = {}
        for directory, dirs, files in os.walk(self.home, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink():
                    result[str(path)] = ("link", os.readlink(path))
                elif path.is_file():
                    result[str(path)] = ("file", path.read_bytes())
                else:
                    result[str(path)] = ("dir",)
        return result

    def installed(self, name="sample", source="owner/repo"):
        directory = self.canonical / name
        directory.mkdir()
        (directory / "SKILL.md").write_text("keep\n")
        state = json.loads(self.lock.read_text()) if self.lock.exists() else {"skills": {}}
        state["skills"][name] = {"source": source}
        self.lock.write_text(json.dumps(state))
        return directory

    def provider(self):
        directory = self.home / ".copilot/skills"
        directory.mkdir(parents=True)
        return directory

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_authored_links_are_idempotent_and_collisions_preserve_data(self):
        local = self.authored / "local"
        local.mkdir()
        (local / "SKILL.md").write_text("local skill\n")
        result = self.run_script("sync-agent-skills.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.canonical / "local").resolve(), local)
        before = self.snapshot()
        result = self.run_script("sync-agent-skills.sh", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.snapshot(), before)
        (self.canonical / "local").unlink()
        self.installed("local")
        before = self.snapshot()
        result = self.run_script("sync-agent-skills.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_manifest_installs_multiple_entries_without_consuming_stdin(self):
        self.manifest.write_text("owner/repo|one\nowner/other|two")
        result = self.run_script("sync-agent-skills.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue((self.canonical / "one/SKILL.md").exists())
        self.assertTrue((self.canonical / "two/SKILL.md").exists())

    def test_dry_run_does_not_link_install_or_uninstall(self):
        self.installed()
        self.manifest.write_text("# disabled: owner/repo|sample\nowner/repo|new\n")
        local = self.authored / "local"
        local.mkdir()
        (local / "SKILL.md").write_text("local\n")
        before = self.snapshot()
        result = self.run_script("sync-agent-skills.sh", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.calls())

    def test_disabled_entries_only_remove_matching_third_party_source(self):
        self.installed()
        self.manifest.write_text("# disabled: different/source|sample\n")
        before = self.snapshot()
        result = self.run_script("sync-agent-skills.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.calls())
        self.manifest.write_text("# disabled: owner/repo|sample\n")
        result = self.run_script("sync-agent-skills.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.canonical / "sample").exists())

    def test_manifest_paths_duplicates_and_authored_names_are_rejected_before_changes(self):
        local = self.authored / "local"
        local.mkdir()
        (local / "SKILL.md").write_text("local\n")
        for invalid in ("owner/repo|../escape", "owner/repo|--all", "-option/repo|skill",
                        "owner/repo|good\nother/repo|good", "# disabled: owner/repo|local"):
            with self.subTest(invalid=invalid):
                self.manifest.write_text("owner/repo|first\n" + invalid + "\n")
                before = self.snapshot()
                for script in ("sync-agent-skills.sh", "audit-skills.sh"):
                    result = self.run_script(script)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.snapshot(), before)
                self.assertFalse(self.calls())

    def test_install_failure_or_false_success_is_not_reported_as_success(self):
        self.manifest.write_text("owner/repo|sample\n")
        for flag in ("SKILLS_TEST_FAIL", "SKILLS_TEST_NOOP"):
            with self.subTest(flag=flag):
                self.env[flag] = "1"
                result = self.run_script("sync-agent-skills.sh")
                self.env.pop(flag)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)

    def test_invalid_lockfile_is_preserved(self):
        for content in ("bad json", '{"skills":{"../escape":{"source":"owner/repo"}}}'):
            with self.subTest(content=content):
                self.lock.write_text(content)
                before = self.snapshot()
                for script, args in (("sync-agent-skills.sh", ()),
                                     ("audit-skills.sh", ("--fix-lockfile",))):
                    result = self.run_script(script, *args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.snapshot(), before)

    def test_audit_is_read_only_and_repair_preserves_real_provider_folders(self):
        self.installed()
        directory = self.provider() / "sample"
        directory.mkdir()
        (directory / "custom.txt").write_text("do not delete")
        before = self.snapshot()
        for args in ((), ("--fix-symlinks",)):
            result = self.run_script("audit-skills.sh", *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("left unchanged", result.stdout)
            self.assertEqual(self.snapshot(), before)

    def test_repair_creates_and_corrects_managed_links_but_preserves_foreign_links(self):
        skill = self.installed()
        provider = self.provider()
        link = provider / "sample"
        result = self.run_script("audit-skills.sh", "--fix-symlinks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), skill)
        link.unlink()
        link.symlink_to(self.canonical / "missing")
        result = self.run_script("audit-skills.sh", "--fix-symlinks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), skill)
        link.unlink()
        foreign = self.root / "foreign"
        foreign.mkdir()
        link.symlink_to(foreign)
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--fix-symlinks")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_last_removed_skill_cleans_only_managed_dangling_links(self):
        provider = self.provider()
        managed = provider / "gone"
        managed.symlink_to("../../.agents/skills/gone")
        foreign = provider / "foreign"
        foreign.symlink_to(self.root / "missing")
        result = self.run_script("audit-skills.sh", "--fix-symlinks", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(managed.is_symlink())
        self.assertTrue(foreign.is_symlink())
        self.assertIn("warning(s)", result.stdout)
        self.assertNotIn("Skills ok", result.stdout)

    def test_stale_cleanup_preserves_real_content_and_similarly_named_directories(self):
        self.installed()
        candidate = self.home / ".cursor"
        (candidate / "skills").mkdir(parents=True)
        (candidate / "skills/sample").symlink_to(self.canonical / "sample")
        backup = candidate / "skills-backup"
        backup.mkdir()
        (backup / "keep").write_text("precious")
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--clean-stale")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        (backup / "keep").unlink()
        backup.rmdir()
        (candidate / "skills/custom.txt").write_text("precious")
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--clean-stale", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("warning(s)", result.stdout)

    def test_stale_cleanup_removes_only_links_and_empty_directories_even_in_quiet_mode(self):
        skill = self.installed()
        candidate = self.home / ".cursor"
        (candidate / "skills/empty").mkdir(parents=True)
        (candidate / "skills/sample").symlink_to(skill)
        before = self.snapshot()
        result = self.run_script("audit-skills.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.run_script("audit-skills.sh", "--clean-stale", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(candidate.exists())
        self.assertEqual((skill / "SKILL.md").read_text(), "keep\n")
        self.assertIn("change(s)", result.stdout)

    def test_lockfile_ghost_fix_preserves_installed_records_and_symlinked_lockfiles(self):
        self.installed()
        state = json.loads(self.lock.read_text())
        state["skills"]["ghost"] = {"source": "owner/repo"}
        self.lock.write_text(json.dumps(state))
        result = self.run_script("audit-skills.sh", "--fix-lockfile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(json.loads(self.lock.read_text())["skills"]), ["sample"])
        external = self.root / "external-lock.json"
        external.write_text(json.dumps(state))
        self.lock.unlink()
        self.lock.symlink_to(external)
        result = self.run_script("audit-skills.sh", "--fix-lockfile")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.lock.is_symlink())
        self.assertEqual(json.loads(external.read_text()), state)

    def test_external_provider_and_stale_directory_symlinks_are_preserved(self):
        self.installed()
        foreign = self.root / "foreign"
        (foreign / "skills").mkdir(parents=True)
        (foreign / "skills/sample").symlink_to(self.canonical / "sample")
        provider = self.home / ".copilot"
        provider.mkdir()
        (provider / "skills").symlink_to(foreign)
        (self.home / ".cursor").symlink_to(foreign)
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--fix-symlinks", "--clean-stale")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((foreign / "skills/sample").is_symlink())
        self.assertFalse((foreign / "sample").exists())

    def test_stale_inspection_failure_preserves_all_content(self):
        self.installed()
        candidate = self.home / ".cursor"
        (candidate / "skills").mkdir(parents=True)
        (candidate / "skills/sample").symlink_to(self.canonical / "sample")
        find = self.bin / "find"
        find.write_text("#!/bin/bash\necho 'Simulated traversal failure' >&2\nexit 1\n")
        find.chmod(0o755)
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--clean-stale")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not inspect", result.stdout)
        self.assertIn("Simulated traversal failure", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_sourced_audit_ignores_repair_options(self):
        self.installed()
        self.provider()
        before = self.snapshot()
        result = subprocess.run(
            ["/bin/bash", "-c", '''
ISSUES=0; WARNINGS=0; BOLD=""; RED=""; YELLOW=""; GREEN=""; RESET=""
section_start() { SECTION_ISSUES=0; SECTION_WARNINGS=0; }
section_end() { :; }
ok() { :; }
warn() { WARNINGS=$((WARNINGS + 1)); SECTION_WARNINGS=$((SECTION_WARNINGS + 1)); }
attn() { warn "$@"; }
fail() { ISSUES=$((ISSUES + 1)); SECTION_ISSUES=$((SECTION_ISSUES + 1)); }
source "$1" --fix-symlinks --fix-lockfile --clean-stale
echo "issues=$ISSUES"
''', "test", str(REPO / "scripts/audit-skills.sh")],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("issues=1", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_audit_flag_fails_without_changes(self):
        before = self.snapshot()
        result = self.run_script("audit-skills.sh", "--unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
