"""Static checks for repository rules that agents have broken before."""

from pathlib import Path, PurePosixPath
import re
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[2]
THIS_FILE = PurePosixPath("scripts/tests") / Path(__file__).name

DOCUMENTED_SCRIPTS = ("scripts/*.sh", "scripts/lib/*.sh", "scripts/package-manager/*.sh", "macos/*.sh")
ALIAS_FILES = ("bash/aliases", "bash/bashrc", "zsh/zshrc", "zsh/zlogin", "zsh/conf.d/*.zsh")
BASH_FILES = ("install", "*.sh", "bash/aliases", "bash/bashrc")



def declaration_with(flag):
    return re.compile(
        r"(?:^|[;&|({]|\b(?:then|do|else)\s)\s*(?:declare|local|typeset)"
        r"(?:\s+-[a-zA-Z]+)*\s+-[a-zA-Z]*" + flag
    )


BASH4_ONLY = {
    "associative array": declaration_with("A"),
    "nameref": declaration_with("n"),
    "global declare": declaration_with("g"),
    "mapfile/readarray": re.compile(r"\b(mapfile|readarray)\b"),
    "case modification": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?(,,?|\^\^?)[^}]*\}"),
    "parameter transformation": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?@[QEPAaKUuLk]\}"),
    "negative array index": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\[-[0-9]+\]\}"),
    "[[ -v ]]": re.compile(r"\[\[\s+!?\s*-v\s"),
    "append-both redirect": re.compile(r"&>>"),
    "pipe stderr": re.compile(r"\|&"),
    "coproc": re.compile(r"\bcoproc\b"),
    "wait -n": re.compile(r"\bwait\s+-n\b"),
}
HARDCODED_HOME = re.compile(r"/Users/[A-Za-z0-9._-]+")
SKILL_COUPLING = re.compile(r"dotfiles|sync-agent-|audit-skills|scripts/tests", re.IGNORECASE)
DOTFILES_REF = re.compile(r"(?:\$HOME|\$\{HOME\}|~)/\.dotfiles/([A-Za-z0-9_./-]+)")


def repo_files(*patterns):
    """Tracked and unignored untracked files, so new files are checked before commit."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "-co", "--exclude-standard", "--", *patterns],
        cwd=REPO, capture_output=True, check=True,
    )
    paths = sorted({PurePosixPath(p) for p in result.stdout.decode().split("\0") if p})
    return [p for p in paths if (REPO / p).is_file() and not re.fullmatch(r"handoff-.*\.md", str(p))]


def text_lines(path):
    try:
        text = (REPO / path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return list(enumerate(text.splitlines(), start=1))


def code_part(line):
    stripped = line.lstrip()
    return "" if stripped.startswith("#") else line


class RepoRulesTests(unittest.TestCase):
    def assert_no_violations(self, violations, rule):
        self.assertEqual(violations, [], f"{rule}:\n" + "\n".join(violations))

    def test_every_script_is_listed_in_agents_md(self):
        agents = (REPO / "AGENTS.md").read_text()
        missing = [str(p) for p in repo_files(*DOCUMENTED_SCRIPTS) if f"`{p}`" not in agents]
        self.assert_no_violations(missing, "Add these scripts to the AGENTS.md structure table")

    def test_shell_config_references_existing_repo_files(self):
        broken = []
        for path in repo_files(*ALIAS_FILES):
            for number, line in text_lines(path):
                for match in DOTFILES_REF.finditer(code_part(line)):
                    target = match.group(1).rstrip(".")
                    if not (REPO / target).exists():
                        broken.append(f"{path}:{number}: ~/.dotfiles/{target}")
        self.assert_no_violations(broken, "Shell config points to repository files that do not exist")

    def test_bash_scripts_avoid_bash4_only_syntax(self):
        found = []
        for path in repo_files(*BASH_FILES):
            for number, line in text_lines(path):
                code = code_part(line)
                for name, pattern in BASH4_ONLY.items():
                    if pattern.search(code):
                        found.append(f"{path}:{number}: {name}: {line.strip()}")
        self.assert_no_violations(found, "Bash 4+ syntax breaks macOS's bundled Bash 3.2")

    def test_no_hardcoded_home_paths(self):
        found = []
        for path in repo_files():
            if path == THIS_FILE or path.suffix in {".plist", ".zip"}:
                continue
            for number, line in text_lines(path):
                if HARDCODED_HOME.search(line):
                    found.append(f"{path}:{number}: {line.strip()}")
        self.assert_no_violations(found, "Use $HOME or ~ instead of /Users/<name>")

    def test_repo_authored_skills_do_not_reference_this_repository(self):
        found = []
        for path in repo_files("agents/skills/"):
            # dotfiles-* skills exist to run this repository's commands from other repositories.
            if path.parent == PurePosixPath("agents/skills") or path.parts[2].startswith("dotfiles-"):
                continue
            for number, line in text_lines(path):
                if SKILL_COUPLING.search(line):
                    found.append(f"{path}:{number}: {line.strip()}")
        self.assert_no_violations(found, "Skills must stand alone without repository-specific references")


class RuleCheckSelfTests(unittest.TestCase):
    def test_bash4_patterns_match_known_bad_and_spare_portable_forms(self):
        bad = [
            "declare -A seen=()", "local -a -A x", "if true; then local -n r=$1", "local -n ref=$1", "declare -g X=1",
            "mapfile -t lines < f", "echo ${name,,}", "echo ${name^}", "echo ${arr[0]^^}",
            "echo ${name,,P}", "echo ${name^a}",
            "echo ${v@Q}", "echo ${arr[-1]}", "[[ -v name ]]", "cmd &>> log", "cmd |& tee",
            "coproc worker", "wait -n",
        ]
        good = [
            "declare -a list=()", "local name=$1", "echo ${name:-x}", "echo ${#arr[@]}",
            "echo ${name: -1}", "echo ${!ref}", "[[ -n $v ]]", "cmd &> log", "cmd 2>&1 | tee",
            "wait", "readlink -n", "echo ${x//,/ }", "printf -v out x",
            "lsregister -domain local -domain user", "echo local -n",
        ]
        def matches(line):
            return [name for name, p in BASH4_ONLY.items() if p.search(line)]
        for line in bad:
            self.assertTrue(matches(line), line)
        for line in good:
            self.assertEqual(matches(line), [], line)

    def test_comments_are_ignored_but_code_is_not(self):
        self.assertEqual(code_part("  # declare -A old"), "")
        self.assertEqual(code_part("x=1  # note"), "x=1  # note")

    def test_dotfiles_reference_pattern_extracts_repo_path(self):
        cases = {
            'alias a="bash ~/.dotfiles/scripts/audit.sh"': "scripts/audit.sh",
            'source "$HOME/.dotfiles/zsh/conf.d/env.zsh"': "zsh/conf.d/env.zsh",
            "x=${HOME}/.dotfiles/bash/aliases": "bash/aliases",
        }
        for line, expected in cases.items():
            self.assertEqual(DOTFILES_REF.search(line).group(1), expected)

    def test_home_and_coupling_patterns(self):
        self.assertTrue(HARDCODED_HOME.search("cd /Users/someone/x"))
        self.assertFalse(HARDCODED_HOME.search("never `/Users/<username>/...`"))
        self.assertTrue(SKILL_COUPLING.search("Run ~/.dotfiles/install"))
        self.assertFalse(SKILL_COUPLING.search("Run the project's test suite"))


if __name__ == "__main__":
    unittest.main()
