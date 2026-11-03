"""The cheat sheet discovers commands without executing them or writing files."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_command_help import ALIASES, REPO, Sandbox, script_commands

sys.path.insert(0, str(REPO / "scripts/lib"))
import command_help


class DotfilesHelpTests(unittest.TestCase):
    def test_command_help_reuses_shared_description_cautions_and_examples(self):
        for name in script_commands():
            with self.subTest(name=name):
                result = self.run_help("--help", command=name)
                text = " ".join(result.stdout.split())
                info = command_help.command_info(name)
                self.assertIn(info.purpose, text)
                self.assertIn(info.caution, text)
                for example in info.examples:
                    if example != name:
                        self.assertIn(example, text)
                self.assertNotIn("{summary}", text)
                self.assertNotIn("{examples}", text)

    def run_help(self, *args, command="dotfiles-help", expected=0, columns=80):
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            before = sorted(str(path.relative_to(sandbox.root))
                            for path in sandbox.root.rglob("*"))
            result = subprocess.run(
                ["/bin/bash", "-c", f'source "$HOME/.aliases"\n{command} "$@"', command, *args],
                cwd=sandbox.project, env={**sandbox.env, "COLUMNS": str(columns)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertEqual(sandbox.tool_calls(), "")
            self.assertEqual(sorted(str(path.relative_to(sandbox.root))
                                    for path in sandbox.root.rglob("*")), before)
            return result

    def test_overview_covers_every_script_command_and_native_pass_through(self):
        overview = self.run_help().stdout
        candidates = self.run_help("--list").stdout
        names = {line.split(":", 1)[0] for line in candidates.splitlines()}
        for name in {*script_commands(), "pm", "pmx"}:
            with self.subTest(name=name):
                self.assertIn(name, names)
                self.assertIn(name, overview)
        self.assertIn("! pma", overview)
        self.assertIn("Read-only reference", overview)
        self.assertIn("examples are not executed", overview)

    def test_package_shortcut_matches_topic_and_does_not_detect_a_manager(self):
        topic = self.run_help("packages").stdout
        self.assertEqual(self.run_help(command="pmh").stdout, topic)
        self.assertIn("$ pml ../library", topic)
        self.assertIn("$ pma --dry-run", topic)
        self.assertIn("Applies fixes immediately by default", topic)
        self.assertNotIn("$ wipe", topic)

    def test_shell_reference_covers_all_repo_aliases_without_cluttering_overview(self):
        names = {line.split(":", 1)[0] for line in self.run_help("--list").stdout.splitlines()}
        aliases = re.findall(r"^\s*alias (?:(?:--|-g) )?([^=\s]+)=", ALIASES.read_text(), re.M)
        self.assertGreater(len(aliases), 40)
        self.assertTrue(set(aliases).issubset(names), set(aliases) - names)
        shell = self.run_help("shell").stdout
        self.assertIn("shell / Navigation", shell)
        self.assertIn("shell / Destructive and session actions", shell)
        self.assertIn("! rr", shell)
        self.assertNotIn("$ type emptytrash", shell)
        self.assertIn("$ files sizes G cache", shell)
        self.assertIn("Zsh global alias", shell)
        self.assertNotIn("─ shell / Git", shell)
        self.assertNotIn("$ g st", shell)
        overview = self.run_help().stdout
        self.assertIn("dotfiles-help shell", overview)
        self.assertNotIn("! emptytrash", overview)
        self.assertNotIn("! rr ", overview)

    def test_git_and_zsh_tips_are_searchable_and_show_correct_cautions(self):
        git = self.run_help("git", "unstage").stdout
        self.assertIn("$ git unstage README.md", git)
        self.assertIn("working-tree edits remain", git)
        self.assertNotIn("$ git st", git)
        self.assertEqual(self.run_help("git unstage").stdout, git)
        self.assertIn("Ctrl-R", self.run_help("zsh").stdout)
        self.assertIn("Not a secrecy guarantee", self.run_help("leading-space").stdout)
        self.assertNotIn("$ Press", self.run_help("Ctrl-R").stdout)
        self.assertIn("Ctrl-R", self.run_help("fuzzy-search").stdout)
        names = self.run_help("git", "--list").stdout
        self.assertIn("unstage:", names)
        self.assertNotIn("git unstage:", names)
        self.assertIn("$ g st", self.run_help("git").stdout)

    @unittest.skipUnless(shutil.which("zsh"), "zsh is not installed")
    def test_global_aliases_filter_output_and_allow_quoted_literals(self):
        cases = (
            ("printf 'Cache\\nother\\nCACHE\\n' G cache", "Cache\nCACHE\n"),
            ("printf 'first\\nsecond\\n' H -n 1", "first\n"),
            ("printf 'first\\nsecond\\n' T -n 1", "second\n"),
            ("printf 'paged\\n' L", "pager\npaged\n"),
            ("printf '%s\\n' 'G' 'H' 'T' 'L'", "G\nH\nT\nL\n"),
        )
        with tempfile.TemporaryDirectory() as root:
            sandbox = Sandbox(root)
            for command, expected in cases:
                with self.subTest(command=command):
                    script = sandbox.root / "example.zsh"
                    script.write_text(command + "\n")
                    result = subprocess.run(
                        ["zsh", "-f", "-c",
                         'less() { printf "pager\\n"; cat; }\nsource "$HOME/.aliases"\nsource "$1"',
                         "alias-test", str(script)],
                        cwd=sandbox.project, env=sandbox.env,
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")

    def test_overview_places_git_and_zsh_links_in_shell_section(self):
        overview = self.run_help().stdout
        shell = overview.split("─ shell -", 1)[1].split("─ Help", 1)[0]
        self.assertIn("dotfiles-help git", shell)
        self.assertIn("dotfiles-help zsh", shell)
        self.assertNotIn("─ git -", overview)
        self.assertNotIn("─ zsh -", overview)

    def test_individual_sheets_separate_entries_and_keep_groups_together(self):
        sheet = self.run_help("zsh").stdout
        self.assertEqual(sheet.count("─ zsh / History and completion"), 1)
        self.assertLess(sheet.index("leading-space"), sheet.index("─ zsh / Files and directories"))
        self.assertRegex(sheet, r"select with Enter\.\n\n  • Up/Down")
        self.assertIn("  • Ctrl-R", sheet)
        self.assertIn("\n    Fuzzy-search command history\n", sheet)
        self.assertNotIn("─ Help", sheet)
        for columns in (60, 80):
            with self.subTest(columns=columns):
                lines = self.run_help("zsh", columns=columns).stdout.splitlines()
                # The shared fixed-width section rules are independent of terminal width.
                body = [line for line in lines if line.startswith("    ")]
                self.assertTrue(body)
                self.assertTrue(all(len(line) <= columns for line in body), body)

    def test_examples_show_usage_without_repeating_the_command_heading(self):
        for name in ("clearlogs", "emptytrash", "cleanupds", "git st", "path"):
            with self.subTest(name=name):
                sheet = self.run_help(name).stdout
                self.assertIn(name, sheet)
                self.assertNotIn(f"$ {name}\n", sheet)
                self.assertNotIn("$ type ", sheet)
        self.assertIn("$ rr ./unwanted-file", self.run_help("rr").stdout)
        self.assertIn("$ pml ../library", self.run_help("pml").stdout)
        self.assertIn("$ files sizes", self.run_help("files").stdout)

    def test_node_modules_shortcut_points_to_previewable_cleanup(self):
        sheet = self.run_help("nrr").stdout
        self.assertIn("without confirmation", sheet)
        self.assertIn("$ wipe_clean --dry-run", sheet)
        self.assertIn("$ clean_caches --dry-run", sheet)
        self.assertIn("both cover more than node_modules", " ".join(sheet.split()))

    def test_command_details_and_task_search(self):
        detail = self.run_help("pml").stdout
        self.assertIn("$ pml ../library", detail)
        self.assertIn("$ pml --undo ../library", detail)
        self.assertIn("npm and pnpm 11+", detail)
        self.assertNotIn("$ pma", detail)
        search = self.run_help("disk", "space").stdout
        self.assertIn("$ files sizes", search)
        self.assertNotIn("$ pml", search)
        self.assertIn("$ pmu --dry-run", self.run_help("UPDATE", command="pmh").stdout)

    def test_topic_and_package_completion_lists_are_scoped(self):
        packages = self.run_help("--list", command="pmh").stdout
        names = {line.split(":", 1)[0] for line in packages.splitlines()}
        self.assertEqual(names, {"pm", "pmx", "pmi", "pmr", "pmu", "pml", "pmc", "pma", "pmh"})
        self.assertEqual(self.run_help("packages", "--list").stdout, packages)
        self.assertIn("cleanup:", self.run_help("--list").stdout)
        self.assertNotIn("cleanup:", packages)

    def test_completion_names_distinguish_topics_from_colliding_commands(self):
        lines = self.run_help("--list").stdout.splitlines()
        names = [line.split(":", 1)[0] for line in lines]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("cleanup:Topic - Clean caches, artifacts and branches", lines)
        self.assertIn("shell cleanup:Command - Run cleanupds and then cleanupad", lines)
        topic = self.run_help("cleanup").stdout
        command = self.run_help("shell cleanup").stdout
        self.assertIn("Preview global developer caches", topic)
        self.assertNotIn("Run cleanupds and then cleanupad", topic)
        self.assertIn("Run cleanupds and then cleanupad", command)
        self.assertNotIn("Preview global developer caches", command)

    def test_unknown_search_and_options_fail_without_actions(self):
        for args in (("nothing-matches-this",), ("--unknown",)):
            with self.subTest(args=args):
                result = self.run_help(*args, expected=2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("Error: "), result.stderr)
        result = self.run_help("cleanup", command="pmh", expected=2)
        self.assertIn("only covers the packages topic", result.stderr)
        completion = self.run_help("nothing-matches-this", "--list")
        self.assertEqual(completion.stdout, "")
        self.assertEqual(completion.stderr, "")

    def test_help_takes_precedence_over_topics_and_invalid_options(self):
        for command in ("dotfiles-help", "pmh"):
            result = self.run_help("cleanup", "--unknown", "--help", command=command)
            self.assertTrue(result.stdout.startswith("Usage: dotfiles-help"))
            self.assertIn("--list", result.stdout)
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
