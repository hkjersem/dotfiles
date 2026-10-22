"""Tests that every dotfiles command has safe --help output, and that zsh completes its flags."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
ALIASES = REPO / "bash" / "aliases"
COMPLETIONS = REPO / "zsh" / "conf.d" / "completions.zsh"
SCRIPT_FUNCTION = re.compile(r'^([\w-]+)\(\) \{ (?:bash|python3) "\$HOME/\.dotfiles/scripts/[^"]+" "\$@"; \}$', re.M)
# Tools a help request must never run.
TOOLS = ("bun", "corepack", "fnm", "git", "node", "npm", "npx", "pnpm")


def script_commands():
    return sorted(SCRIPT_FUNCTION.findall(ALIASES.read_text()))


def completed_commands():
    match = re.search(r"^typeset -ga dotfiles_commands=\(([^)]*)\)", COMPLETIONS.read_text(), re.M)
    return sorted(match.group(1).split()) if match else []


class Sandbox:
    """An isolated HOME linking this repository, a project folder, and failing stand-ins for TOOLS."""

    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".dotfiles").symlink_to(REPO, target_is_directory=True)
        (self.home / ".aliases").symlink_to(ALIASES)
        self.project = self.root / "project"
        (self.project / "node_modules" / "left").mkdir(parents=True)
        (self.project / "notes.txt").write_text("kept\n")
        self.calls = self.root / "tool-calls"
        tools = self.root / "bin"
        tools.mkdir()
        for tool in TOOLS:
            path = tools / tool
            path.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{self.calls}"\nexit 1\n')
            path.chmod(0o755)
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(tools), str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
            "TERM": "xterm",
            "LANG": "en_US.UTF-8",
        }

    def files(self):
        return sorted(str(p.relative_to(self.project)) for p in self.project.rglob("*"))

    def tool_calls(self):
        return self.calls.read_text() if self.calls.exists() else ""


class CommandHelpTests(unittest.TestCase):
    def test_every_script_command_is_completed(self):
        self.assertGreater(len(script_commands()), 10)
        self.assertEqual(completed_commands(), script_commands())

    def test_help_prints_usage_without_running_tools_or_changing_files(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            before = sandbox.files()
            for command in script_commands():
                for flag in ("--help", "-h"):
                    with self.subTest(command=command, flag=flag):
                        result = subprocess.run(
                            ["/bin/bash", "-c", f'source "$HOME/.aliases"\n{command} {flag}'],
                            cwd=sandbox.project, env=sandbox.env, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=30,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertRegex(result.stdout, r"(?i)^usage: ")
                        # _gnu_generic only finds flags on indented lines that start with a dash.
                        self.assertRegex(result.stdout, r"(?m)^\s+(-\w, )?--help\b")
                        self.assertEqual(sandbox.tool_calls(), "")
                        self.assertEqual(sandbox.files(), before)

    def run_wipe(self, sandbox, *args):
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$HOME/.aliases"\nwipe "$@"', "wipe", *args],
            cwd=sandbox.project, env=sandbox.env, capture_output=True, text=True, timeout=30,
        )

    def test_wipe_shows_help_or_rejects_unknown_options_without_deleting(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            (sandbox.project / "build").mkdir()
            before = sandbox.files()
            for args in (("build", "--help"), ("build", "-h"), ("--dir", "build", "-h")):
                with self.subTest(args=args):
                    result = self.run_wipe(sandbox, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertRegex(result.stdout, r"^Usage: wipe ")
                    self.assertEqual(sandbox.files(), before)
            for args in (("build", "--dirs"), ("--dir", "build"), ("build", "--dir", "extra")):
                with self.subTest(args=args):
                    result = self.run_wipe(sandbox, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Usage: wipe", result.stderr)
                    self.assertEqual(sandbox.files(), before)

    def test_wipe_deletes_matching_folders_outside_node_modules(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            (sandbox.project / "build").mkdir()
            (sandbox.project / "app" / "build").mkdir(parents=True)
            (sandbox.project / "node_modules" / "build").mkdir()
            result = self.run_wipe(sandbox, "build")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sandbox.files(), [
                "app", "node_modules", "node_modules/build", "node_modules/left", "notes.txt"])


@unittest.skipUnless(shutil.which("zsh"), "zsh is not installed")
class CompletionTests(unittest.TestCase):
    # Replaces compadd to print the offered matches, and binds ^X to clear the line and print an end marker,
    # as in zsh-capture-completion.
    ZSHRC = r"""
unsetopt PROMPT_SP
PS1= RPROMPT=
autoload -Uz compinit && compinit -u -d "$ZDOTDIR/.zcompdump"
source {aliases_zsh}
source {completions}
compadd() {{
  [[ ${{@[1,(i)(-|--)]}} == *-(O|A|D)\ * ]] && {{ builtin compadd "$@"; return; }}
  typeset -a hits; builtin compadd -A hits "$@"
  (( $#hits )) && print -l -- ${{hits/#/HIT:}}
}}
finish() {{ BUFFER=; print -n -- $'\n<END>\n'; }}
zle -C capture-complete complete-word _main_complete
zle -N finish
bindkey '^I' capture-complete
bindkey '^X' finish
"""
    DRIVER = r"""
zmodload zsh/zpty
zpty z zsh -i
for command in "$@"; do
  zpty -w -n z "$command --"$'\t\x18'
  print -r -- "CMD:$command"
  while zpty -r z line; do
    [[ $line == *'<END>'* ]] && break
    print -r -- "${line//$'\r'/}"
  done
done
zpty -d z
"""

    def complete(self, commands):
        """Return the flags zsh offers after `<command> --` for each command."""
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            zdotdir = sandbox.root / "zdotdir"
            zdotdir.mkdir()
            (zdotdir / ".zshrc").write_text(self.ZSHRC.format(
                aliases_zsh=REPO / "zsh" / "conf.d" / "aliases.zsh", completions=COMPLETIONS))
            driver = sandbox.root / "driver.zsh"
            driver.write_text(self.DRIVER)
            result = subprocess.run(
                ["zsh", "-f", str(driver), *commands], cwd=sandbox.project,
                env={**sandbox.env, "ZDOTDIR": str(zdotdir)}, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sandbox.tool_calls(), "")
        offered, current = {}, None
        for line in result.stdout.splitlines():
            if line.startswith("CMD:"):
                current = offered.setdefault(line[4:], set())
            elif "HIT:" in line and current is not None:
                current.add(line.split("HIT:", 1)[1].strip())
        return offered

    def test_flags_are_completed_from_help(self):
        offered = self.complete(script_commands())
        self.assertEqual(
            offered["pmu"], {"--dry-run", "--yes", "--force", "--major", "--cooldown", "--actions", "--help"})
        self.assertEqual(offered["wipe"], {"--file", "--dir", "--help"})
        self.assertEqual(offered["pml"], {"--undo", "--help"})
        for command in script_commands():
            with self.subTest(command=command):
                self.assertIn("--help", offered.get(command, set()))



if __name__ == "__main__":
    unittest.main()
