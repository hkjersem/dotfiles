"""Tests that every dotfiles command has safe --help output, and that zsh completes and highlights its flags."""

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
FLAG_HIGHLIGHT = REPO / "zsh" / "conf.d" / "flag-highlight.zsh"
SCRIPT_FUNCTION = re.compile(
    r'^([\w-]+)\(\) \{ (?:bash|python3) "\$HOME/\.dotfiles/(?:scripts|macos)/[^"]+"'
    r'(?: --[\w-]+)* "\$@"; \}$', re.M)
# Tools a help request must never run.
TOOLS = ("bun", "corepack", "fnm", "git", "node", "npm", "npx", "pnpm",
         "softwareupdate", "mas", "brew", "sudo", "osascript", "defaults")


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
        self.assertTrue({
            "dotfiles-update", "dotfiles-install", "dotfiles-defaults",
            "dotfiles-audit", "dotfiles-profile",
        }.issubset(script_commands()))
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

    def test_dotfiles_update_shows_help_or_rejects_unknown_options_without_updating(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)

            def run(*args):
                return subprocess.run(
                    ["/bin/bash", "-c", 'shopt -s expand_aliases\nsource "$HOME/.aliases"\ndotfiles-update "$@"',
                     "dotfiles-update", *args],
                    cwd=sandbox.project, env=sandbox.env, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL, timeout=30,
                )

            for flag in ("--help", "-h"):
                with self.subTest(flag=flag):
                    result = run(flag)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertRegex(result.stdout, r"^Usage: dotfiles-update")
            result = run("--nodefaults")
            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown option: --nodefaults", result.stderr)
            self.assertEqual(sandbox.tool_calls(), "")
            self.assertFalse((sandbox.home / ".config" / "dotfiles" / "profile").exists())

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

    def test_dotfiles_install_runs_setup_in_order_and_stops_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            expected = [
                f"{sandbox.home}/.dotfiles/scripts/machine-profile.sh",
                f"{sandbox.home}/.dotfiles/install --no-audit",
                f"{sandbox.home}/.dotfiles/macos/install.sh --no-defaults",
            ]
            mock = sandbox.root / "bin" / "bash"
            mock.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$INSTALL_CALLS"\n'
                '[ "$*" != "$INSTALL_FAIL" ] || exit 7\n'
            )
            mock.chmod(0o755)
            for failure in ("", *expected):
                with self.subTest(failure=failure):
                    if sandbox.calls.exists():
                        sandbox.calls.unlink()
                    result = subprocess.run(
                        ["/bin/bash", str(REPO / "scripts/dotfiles-install.sh")],
                        cwd=sandbox.project,
                        env={**sandbox.env, "INSTALL_CALLS": str(sandbox.calls), "INSTALL_FAIL": failure},
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 7 if failure else 0, result.stderr)
                    count = expected.index(failure) + 1 if failure else len(expected)
                    self.assertEqual(sandbox.tool_calls().splitlines(), expected[:count])


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
suffix=$1
shift
for command in "$@"; do
  zpty -w -n z "$command$suffix"$'\t\x18'
  print -r -- "CMD:$command"
  while zpty -r z line; do
    [[ $line == *'<END>'* ]] && break
    print -r -- "${line//$'\r'/}"
  done
done
zpty -d z
"""

    def complete(self, commands, suffix=" --"):
        """Return the completions zsh offers after each command and suffix."""
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            zdotdir = sandbox.root / "zdotdir"
            zdotdir.mkdir()
            (zdotdir / ".zshrc").write_text(self.ZSHRC.format(
                aliases_zsh=REPO / "zsh" / "conf.d" / "aliases.zsh", completions=COMPLETIONS))
            driver = sandbox.root / "driver.zsh"
            driver.write_text(self.DRIVER)
            result = subprocess.run(
                ["zsh", "-f", str(driver), suffix, *commands], cwd=sandbox.project,
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

    def test_profile_names_are_completed_only_for_the_first_argument(self):
        for suffix, expected in (
            (" ", {"personal", "work"}),
            (" p", {"personal"}),
            (" w", {"work"}),
            (" personal ", set()),
        ):
            with self.subTest(suffix=suffix):
                offered = self.complete(["dotfiles-profile"], suffix)["dotfiles-profile"]
                self.assertEqual(offered & {"personal", "work"}, expected)

    def test_files_subcommands_and_flags_are_completed(self):
        names = {"count", "large", "changed", "sizes", "empty", "types", "duplicates", "caches", "leftovers"}
        for suffix, expected in ((" ", names), (" c", {"count", "changed", "caches"})):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.complete(["files"], suffix)["files"] & names, expected)
        flags = self.complete(["files"], " large --")["files"]
        self.assertIn("--recursive", flags)
        self.assertIn("--min", flags)
        self.assertIn("--clean", flags)
        self.assertIn("--clean-git", flags)
        self.assertIn("--help", flags)
        self.assertFalse(flags & names)
        self.assertIn("--direct", self.complete(["files"], " sizes --")["files"])
        self.assertIn("--logical", self.complete(["files"], " sizes --")["files"])
        self.assertIn("--cached", self.complete(["files"], " sizes --")["files"])
        self.assertEqual(self.complete(["files"], " caches --")["files"], {"--help", "--limit"})
        self.assertIn("--older-than", self.complete(["files"], " leftovers --")["files"])

    def test_files_exclude_flag_can_be_completed_repeatedly(self):
        offered = self.complete(["files"], " sizes --exclude Library --ex")["files"]
        self.assertIn("--exclude", offered)

    def test_wipe_clean_sort_flag_and_values_are_completed(self):
        self.assertIn("--sort", self.complete(["wipe_clean"], " --")["wipe_clean"])
        self.assertIn("--older-than", self.complete(["wipe_clean"], " --")["wipe_clean"])
        self.assertEqual(self.complete(["wipe_clean"], " --sort ")["wipe_clean"] & {"path", "size"},
                         {"path", "size"})

    def test_cache_cleaner_names_and_pruning_flags_are_completed(self):
        self.assertEqual(self.complete(["clean_caches"], " --")["clean_caches"],
                         {"--only", "--deep", "--dry-run", "--help"})
        names = self.complete(["clean_caches"], " --only ")["clean_caches"]
        self.assertTrue({"npm", "uv", "gradle", "go-modules"}.issubset(names), names)
        flags = self.complete(["prune_branches"], " --")["prune_branches"]
        self.assertTrue({"--include-detached", "--older-than"}.issubset(flags), flags)

    def test_duration_values_offer_examples_and_units_for_typed_numbers(self):
        cases = (("prune_branches", " --stale-after "),
                 ("prune_branches", " --older-than "),
                 ("wipe_clean", " --older-than "),
                 ("files", " leftovers --older-than "),
                 ("files", " changed --since "),
                 ("pmu", " --cooldown "),
                 ("pmu", " --cooldown="),
                 ("audit-repo", " --since "))
        for command, suffix in cases:
            with self.subTest(command=command, suffix=suffix):
                offered = self.complete([command], suffix)[command]
                self.assertTrue({"24h", "7d", "2w"}.issubset(offered), offered)
                self.assertEqual("0" in offered, command == "pmu", offered)
                numbered = self.complete([command], suffix + "14")[command]
                self.assertTrue({"14m", "14h", "14d", "14w"}.issubset(numbered), numbered)

    def test_flags_are_completed_from_help(self):
        offered = self.complete(script_commands())
        self.assertEqual(
            offered["pmu"], {"--dry-run", "--yes", "--force", "--major", "--cooldown", "--actions", "--help"})
        self.assertEqual(offered["wipe"], {"--file", "--dir", "--help"})
        self.assertEqual(offered["pml"], {"--undo", "--help"})
        self.assertEqual(offered["dotfiles-update"], {"--skip-os-updates", "--no-defaults", "--help"})
        self.assertEqual(offered["dotfiles-install"], {"--no-defaults", "--help"})
        self.assertEqual(offered["dotfiles-defaults"], {"--dns", "--help"})
        self.assertEqual(offered["dotfiles-audit"], {"--help"})
        self.assertEqual(offered["dotfiles-profile"], {"--choose", "--help"})
        for command in script_commands():
            with self.subTest(command=command):
                self.assertIn("--help", offered.get(command, set()))


@unittest.skipUnless(shutil.which("zsh"), "zsh is not installed")
class FlagHighlightTests(unittest.TestCase):
    # Stands in for zsh-syntax-highlighting, which is not part of this repository, and prints
    # the text of each highlighted region per buffer.
    DRIVER = r"""
source "$1"; compdef() { :; }; source "$2"; source "$3"
# zsh-syntax-highlighting runs highlighters with this option.
setopt warn_create_global
_zsh_highlight_add_highlight() { print -r -- "MARK:$4:${BUFFER[$1+1,$2]}"; }
shift 3
for BUFFER in "$@"; do
  print -r -- "BUFFER"
  _zsh_highlight_highlighter_dotfiles_flags_paint
done
"""

    def marked(self, *buffers):
        """Return the highlighted text in each buffer."""
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            driver = sandbox.root / "driver.zsh"
            driver.write_text(self.DRIVER)
            result = subprocess.run(
                ["zsh", "-f", str(driver), str(REPO / "zsh" / "conf.d" / "aliases.zsh"), str(COMPLETIONS),
                 str(FLAG_HIGHLIGHT), *buffers],
                cwd=sandbox.project, env=sandbox.env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(sandbox.tool_calls(), "")
        marks = []
        for line in result.stdout.splitlines():
            if line == "BUFFER":
                marks.append([])
            elif line.startswith("MARK:"):
                style, text = line[5:].split(":", 1)
                self.assertEqual(style, "unknown-token")
                marks[-1].append(text)
        self.assertEqual(len(marks), len(buffers))
        return dict(zip(buffers, marks))

    def test_flags_listed_in_help_are_not_marked(self):
        buffers = [f"{command} -h --help" for command in script_commands()]
        self.assertEqual(self.marked(*buffers), {buffer: [] for buffer in buffers})

    def test_flags_missing_from_help_are_marked(self):
        self.assertEqual(self.marked(
            "pmu --dry-run -x --cooldown=3d --nope",
            "pmu -ny",
            "wipe build --dirs",
            "X=1 nocorrect pmu -n --zz",
            "echo pmu --x; pmu --x",
            "pmu -n\nwipe x --nah",
            "dotfiles-update --skip-os-updates --nope",
            "dotfiles-defaults --dns --nope",
            "dotfiles-install --nope",
            "dotfiles-audit --nope",
            "dotfiles-profile --choose --nope",
        ), {
            "pmu --dry-run -x --cooldown=3d --nope": ["-x", "--nope"],
            "pmu -ny": ["-ny"],
            "wipe build --dirs": ["--dirs"],
            "X=1 nocorrect pmu -n --zz": ["--zz"],
            "echo pmu --x; pmu --x": ["--x"],
            "pmu -n\nwipe x --nah": ["--nah"],
            "dotfiles-update --skip-os-updates --nope": ["--nope"],
            "dotfiles-defaults --dns --nope": ["--nope"],
            "dotfiles-install --nope": ["--nope"],
            "dotfiles-audit --nope": ["--nope"],
            "dotfiles-profile --choose --nope": ["--nope"],
        })

    def test_argparse_bundles_are_accepted_but_not_prefixes(self):
        self.assertEqual(self.marked("nosync -rh", "nosync -rx", "nosync --insp", "codex-resume --rec 15"), {
            "nosync -rh": [],
            "nosync -rx": ["-rx"],
            "nosync --insp": ["--insp"],
            "codex-resume --rec 15": ["--rec"],
        })

    def test_pass_through_commands_other_commands_and_operands_are_not_marked(self):
        buffers = ["pmi -D --anything", "pmr --anything", "ls --nope", "pmu -- --zz",
                   'codex-resume --message "a\\\nb" --nope']
        self.assertEqual(self.marked(*buffers), {buffer: [] for buffer in buffers})


if __name__ == "__main__":
    unittest.main()
