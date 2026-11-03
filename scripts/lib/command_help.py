#!/usr/bin/env python3
"""A read-only, searchable reference for custom shell commands."""

import argparse
from dataclasses import dataclass
import shutil
import sys
import textwrap

sys.dont_write_bytecode = True
import output


HELP = """Usage: dotfiles-help [topic | command | search words] [options]
       pmh [command | search words] [options]

{summary}

pmh shows the packages topic.
Use a command name for examples and cautions, or search by what you want to do.

  -h, --help  Show this help
  --packages  Limit results to package-manager commands
  --list      List completion candidates as name:description, one per line

{examples}

Each script command also has its own --help. pm -h is native manager help.
"""

TOPICS = {
    "packages": "Package-manager commands",
    "node": "Node versions and global packages",
    "files": "Inspect and copy files",
    "cleanup": "Clean caches, artifacts and branches",
    "repos": "Repositories and roadmaps",
    "agents": "Agent sessions, sleep and scheduling",
    "setup": "Machine setup and maintenance",
    "shell": "Everyday shell shortcuts",
    "git": "Useful configured Git aliases",
    "zsh": "Interactive shell and fuzzy-finder tips",
}


@dataclass(frozen=True)
class Command:
    name: str
    topic: str
    purpose: str
    caution: str
    examples: tuple[str, ...]
    changes: bool = False
    group: str = ""


COMMANDS = (
    Command("pm", "packages", "Run a project's detected npm, pnpm or Bun",
            "Passes arguments straight through; pm -h is native help, not wrapper help.",
            ("pm run dev", "pm -h"), True),
    Command("pmx", "packages", "Download and execute a package without adding a dependency",
            "Runs package code through npx, pnpm dlx or bunx; it may download to a cache.",
            ("pmx prettier --check .",), True),
    Command("pmi", "packages", "Install dependencies or add a package",
            "Changes dependencies. In pnpm workspaces, --root explicitly selects the root.",
            ("pmi", "pmi -D typescript", "pmi react@19"), True),
    Command("pmr", "packages", "Remove a dependency",
            "Changes dependencies; ambiguous pnpm workspace targets fail rather than guessing.",
            ("pmr lodash", "pmr --root lodash"), True),
    Command("pmu", "packages", "Preview or apply dependency updates with release-age cooldowns",
            "Asks before applying; --yes skips confirmation. --major permits breaking updates; --actions needs pnpm 12.7+.",
            ("pmu --dry-run", "pmu react", "pmu --major", "pmu --actions"), True),
    Command("pml", "packages", "Try changes in a local library, then undo the link",
            "Changes dependency links. Supports npm and pnpm 11+; undo before committing.",
            ("pml ../library", "pml --undo ../library"), True),
    Command("pmc", "packages", "Clean manifest clutter and unused pnpm catalog entries",
            "Can change manifests and catalogs; preview first.",
            ("pmc --dry-run", "pmc"), True),
    Command("pma", "packages", "Audit vulnerabilities and apply supported fixes",
            "Applies fixes immediately by default; Bun only reports. --deep refreshes the pnpm lockfile.",
            ("pma --dry-run", "pma"), True),
    Command("pmh", "packages", "Remember the package-manager wrappers",
            "Read-only. A shortcut for dotfiles-help --packages.",
            ("pmh", "pmh pml", "pmh update")),
    Command("install_node", "node", "Install a Node version and migrate global packages",
            "Changes fnm installations and removes older same-major versions.",
            ("install_node lts", "install_node 22"), True),
    Command("npm_globals_diff", "node", "Compare global npm packages between Node versions",
            "Read-only; the second version defaults to the current Node.",
            ("npm_globals_diff 22", "npm_globals_diff lts 22")),
    Command("npm_release_age", "node", "Check how recently an npm package was released",
            "Read-only; queries the npm registry.",
            ("npm_release_age react", "npm_release_age react@19 --all")),
    Command("files", "files", "Find disk space usage, large files, duplicates and leftovers",
            "Read-only, including files caches and files leftovers; never deletes anything.",
            ("files sizes", "files large -r", "files duplicates -r", "files caches", "files leftovers")),
    Command("nosync", "files", "Inspect or prevent iCloud syncing for folders",
            "Changes folder attributes unless --inspect is given.",
            ("nosync --inspect", "nosync -r --inspect node_modules", "nosync node_modules"), True),
    Command("copy_clean", "files", "Copy or ZIP a project without generated artifacts",
            "Creates a copy or archive; excludes the artifacts also targeted by wipe_clean.",
            ("copy_clean", "copy_clean --zip", "copy_clean --ignore-git"), True),
    Command("clean_caches", "cleanup", "Preview global developer caches and select cleaners",
            "Deletes selected caches after confirmation. Expensive cleaners require --deep.",
            ("clean_caches --dry-run", "clean_caches --only npm,pnpm"), True),
    Command("wipe_clean", "cleanup", "Remove build artifacts and dependency folders",
            "Deletes after confirmation. Preview the paths first; --older-than limits by age.",
            ("wipe_clean --dry-run", "wipe_clean --older-than 30d"), True),
    Command("wipe", "cleanup", "Delete nested files or folders matching a name",
            "Deletes immediately without confirmation; skips node_modules and .git.",
            ("wipe --help", "wipe '*.log' --file"), True),
    Command("prune_branches", "cleanup", "Preview stale Git branches and linked worktrees",
            "Fetches and asks before deleting. Never deletes never-pushed branches; preview first.",
            ("prune_branches --dry-run", "prune_branches --older-than 30d"), True),
    Command("audit-repo", "repos", "Triage Git activity, workspaces, tests and dependencies",
            "Read-only; audit and outdated checks may use the network.",
            ("audit-repo", "audit-repo --workspaces", "audit-repo --skip-security")),
    Command("roadmaps", "repos", "Find due, awaiting-acceptance and in-progress roadmap items",
            "Read-only by default; --sync writes generated indexes for explicitly supplied paths.",
            ("roadmaps", "roadmaps .", "roadmaps --check", "roadmaps --check --strict .",
             "roadmaps --sync ."), True),
    Command("gclone", "repos", "Clone a Git repository and enter its folder",
            "Downloads the repository and changes the current directory.",
            ("gclone https://github.com/owner/repo.git",), True),
    Command("agent-sessions", "agents", "List tracked terminal sessions and restoration status",
            "Read-only; does not resume or delete sessions.",
            ("agent-sessions",)),
    Command("agent-awake", "agents", "Keep the Mac awake while an agent works",
            "Opt-in per Mac. Installation adds agent hooks and a root helper; requires sudo.",
            ("agent-awake --status", "agent-awake --install"), True),
    Command("codex-resume", "agents", "Send or schedule a message to a Codex desktop chat",
            "Can start agent work. Scheduled and recurring runs create user LaunchAgents.",
            ("codex-resume --limits", "codex-resume --time 15:30"), True),
    Command("dotfiles-help", "setup", "Find custom commands by topic, name or task",
            "Read-only; examples are printed, never executed. This is a curated reference, not every plugin command.",
            ("dotfiles-help", "dotfiles-help packages", "dotfiles-help disk space")),
    Command("dotfiles-audit", "setup", "Check installed tools against repository declarations",
            "Read-only; some version checks use the network.",
            ("dotfiles-audit",)),
    Command("dotfiles-update", "setup", "Update installed development tools",
            "Updates tools; the shell wrapper skips macOS preferences by default.",
            ("dotfiles-update", "dotfiles-update --skip-os-updates"), True),
    Command("dotfiles-install", "setup", "Reapply symlinks and bootstrap tools",
            "Changes links and installed tools; does not apply macOS preferences.",
            ("dotfiles-install",), True),
    Command("dotfiles-defaults", "setup", "Apply the repository's macOS preferences",
            "Changes system preferences; review --help before applying.",
            ("dotfiles-defaults --help",), True),
    Command("dotfiles-profile", "setup", "Show or choose this Mac's personal/work profile",
            "May prompt if unset. Saves a machine-local setting; does not uninstall tools.",
            ("dotfiles-profile", "dotfiles-profile --choose"), True),
    Command("mkd", "shell", "Create a directory and enter it",
            "Creates folders and changes the current directory.",
            ("mkd new-project",), True),
    Command("o", "shell", "Open the current folder or a path in Finder",
            "Opens a Finder window.",
            ("o", "o ~/Downloads"), True),
    Command("cpwd", "shell", "Copy the current directory path to the clipboard",
            "Replaces the clipboard contents.",
            ("cpwd",), True),
    Command("c", "shell", "Copy piped text to the clipboard without newlines",
            "Replaces the clipboard contents.",
            ("printf 'hello' | c",), True),
    Command("path", "shell", "Show each PATH directory on a separate line",
            "Read-only.",
            ("path",)),
    Command("reload", "shell", "Reload shell configuration in a fresh login shell",
            "Replaces the current shell; use after changing aliases or completions.",
            ("reload",), True),
    Command("server", "shell", "Serve the current folder over HTTP",
            "Exposes files over the network, not just localhost. Stop with Ctrl-C.",
            ("server 8080",), True),
    Command("..", "shell", "Go up one directory", "Changes the current directory.",
            ("..",), True, "Navigation"),
    Command("...", "shell", "Go up two directories", "Changes the current directory.",
            ("...",), True, "Navigation"),
    Command("....", "shell", "Go up three directories", "Changes the current directory.",
            ("....",), True, "Navigation"),
    Command("-", "shell", "Return to the previous directory", "Alias for cd -.",
            ("-",), True, "Navigation"),
    Command("cd.", "shell", "Enter the real path behind the current symlink",
            "Changes the current directory using readlink -f.",
            ("cd.",), True, "Navigation"),
    Command("l", "shell", "List hidden entries with details and readable sizes",
            "Alias for ls -lahA; excludes the . and .. entries.", ("l",), group="Listing"),
    Command("la", "shell", "List hidden entries with details and readable sizes",
            "Alias for ls -lAh; same options as l.", ("la",), group="Listing"),
    Command("ll", "shell", "List hidden entries with details",
            "Alias for ls -lA.", ("ll",), group="Listing"),
    Command("lt", "shell", "List entries by modification time, newest last",
            "Alias for ls -lhAtr; includes hidden entries.", ("lt",), group="Listing"),
    Command("ld", "shell", "List immediate non-hidden directories",
            "Expands */; zsh reports no matches if there are none.", ("ld",), group="Listing"),
    Command("tree", "shell", "Show a two-level tree respecting .gitignore",
            "Adds -A -C -L 2 --gitignore to tree; requires the tree tool.",
            ("tree",), group="Listing"),
    Command("tree1", "shell", "Show one level of directories",
            "Runs tree -d -L 1.", ("tree1",), group="Listing"),
    Command("tree2", "shell", "Show two levels of directories",
            "Runs tree -d -L 2.", ("tree2",), group="Listing"),
    Command("G", "shell", "Filter output case-insensitively",
            "Zsh global alias for | grep -i. Quote a literal 'G' argument to prevent expansion.",
            ("files sizes G cache",), group="Pipes and text"),
    Command("H", "shell", "Show the first lines of output",
            "Zsh global alias for | head. Quote a literal 'H' argument to prevent expansion.",
            ("files sizes H", "files sizes H -n 5"), group="Pipes and text"),
    Command("T", "shell", "Show the last lines of output",
            "Zsh global alias for | tail. Quote a literal 'T' argument to prevent expansion.",
            ("files sizes T",), group="Pipes and text"),
    Command("L", "shell", "Read output one screen at a time",
            "Zsh global alias for | less. Press q to exit; quote a literal 'L' argument.",
            ("dotfiles-help shell L",), group="Pipes and text"),
    Command("urlencode", "shell", "Encode text for use in a URL",
            "Uses Python quote_plus; spaces become +.", ("urlencode 'hello world'",), group="Pipes and text"),
    Command("ip", "shell", "Look up the public IP address",
            "Makes a DNS query to OpenDNS.", ("ip",), group="Network and system"),
    Command("localip", "shell", "Show the IPv4 address of en0",
            "Only checks en0; other network interfaces may differ.", ("localip",), group="Network and system"),
    Command("systail", "shell", "Stream macOS system logs",
            "Runs log stream --style syslog. Stop with Ctrl-C.", ("systail",), group="Network and system"),
    Command("week", "shell", "Show the ISO week number",
            "Read-only; runs date +%V.", ("week",), group="Network and system"),
    Command("afk", "shell", "Sleep the display",
            "Locks only if macOS requires a password immediately after display sleep.",
            ("afk",), True, "Network and system"),
    Command("flush", "shell", "Flush the directory and DNS caches",
            "Uses sudo and signals mDNSResponder; requires administrator access.",
            ("flush",), True, "Network and system"),
    Command("rsync", "shell", "Copy and synchronise files with verbose, readable output",
            "Adds -vh. Other options can overwrite or delete files; inspect the full command.",
            ("rsync --dry-run -a source/ destination/",), True, "Copying and privileges"),
    Command("_", "shell", "Run a command with sudo",
            "Elevates privileges; inspect the command before running it.",
            ("_ -l",), True, "Copying and privileges"),
    Command("sudo", "shell", "Allow alias expansion after sudo",
            "The trailing space in this alias permits expansion of the following alias.",
            ("sudo -l",), True, "Copying and privileges"),
    Command("g", "git", "Use git with a shorter command name",
            "Passes all arguments to Git; effects depend on the command.",
            ("g st", "g hist"), True, "Daily inspection"),
    Command("nrr", "shell", "Delete nested node_modules folders",
            "Runs wipe node_modules immediately, without confirmation. Prefer wipe_clean --dry-run to preview project artifact cleanup, or clean_caches --dry-run for global caches; both cover more than node_modules.",
            ("wipe_clean --dry-run", "clean_caches --dry-run"), True, "Destructive and session actions"),
    Command("rr", "shell", "Recursively force-delete supplied paths",
            "Alias for rm -rf; no confirmation or Trash. Prefer targeted cleanup commands.",
            ("rr ./unwanted-file",), True, "Destructive and session actions"),
    Command("cleanupds", "shell", "Delete nested .DS_Store files",
            "Deletes immediately below the current directory.",
            ("cleanupds",), True, "Destructive and session actions"),
    Command("cleanupad", "shell", "Delete nested entries named .AppleD*",
            "Recursively deletes matching entries immediately; not limited to directories.",
            ("cleanupad",), True, "Destructive and session actions"),
    Command("cleanup", "shell", "Run cleanupds and then cleanupad",
            "Deletes matching metadata immediately below the current directory.",
            ("cleanup",), True, "Destructive and session actions"),
    Command("emptytrash", "shell", "Attempt to empty local and mounted-volume Trash",
            "Legacy sudo rm alias; permanent deletion, no confirmation. zsh unmatched globs can abort parts of it.",
            ("emptytrash",), True, "Destructive and session actions"),
    Command("clearlogs", "shell", "Delete legacy system logs and download quarantine history",
            "Legacy destructive alias; may fail on current macOS. Not recommended for routine cleanup.",
            ("clearlogs",), True, "Destructive and session actions"),
    Command("rebootapps", "shell", "Restart user applications",
            "Can terminate apps and lose unsaved work; does not reboot the Mac.",
            ("rebootapps",), True, "Destructive and session actions"),
    Command("rebootuserspace", "shell", "Restart the user-space environment",
            "Uses sudo; disrupts sessions and can lose unsaved work. Not a normal shell reload.",
            ("rebootuserspace",), True, "Destructive and session actions"),
    Command("quit", "shell", "Exit the current shell",
            "Ends the shell session.", ("quit",), True, "Destructive and session actions"),
    Command("h", "shell", "Show shell command history",
            "Read-only; history can contain sensitive arguments. Ctrl-R searches it with fzf.",
            ("h",), group="History"),
    Command("history-delete", "shell", "Delete a numbered event from this shell and saved history",
            "Zsh only. Shows the command and asks [y/N] before deleting. Event numbers may change; other open shells can retain and re-save it. Ambiguous duplicates are refused. Native h flags remain unchanged.",
            ("history-delete 222",), True, "History"),
    Command("git st", "git", "See a compact status with the current branch",
            "Configured alias for status -sb; read-only.", ("git st",), group="Daily inspection"),
    Command("git hist", "git", "Explore a compact commit graph",
            "Read-only; press q if the pager opens.", ("git hist -20",), group="Daily inspection"),
    Command("git last", "git", "Show the last commit",
            "Read-only; add -p to see the patch.", ("git last -p",), group="Daily inspection"),
    Command("git whoami", "git", "Check the effective Git identity and its config source",
            "Read-only; useful before committing in personal and work directories.",
            ("git whoami",), group="Daily inspection"),
    Command("git outgoing", "git", "See local commits not yet pushed",
            "Read-only; requires a configured upstream. Does not fetch.",
            ("git outgoing",), group="Remote changes"),
    Command("git incoming", "git", "Fetch remotes and see upstream commits not in your branch",
            "Updates/prunes remote-tracking refs; does not merge. Requires an upstream.",
            ("git incoming",), True, "Remote changes"),
    Command("git incoming-origin", "git", "See origin default-branch commits missing locally",
            "Fetches/prunes remotes; requires origin/HEAD. Does not merge.",
            ("git incoming-origin",), True, "Remote changes"),
    Command("git whatsnew", "git", "Inspect commits since the previous HEAD position",
            "Uses HEAD@{1}..; that position may be a checkout or commit, not necessarily the last pull.",
            ("git whatsnew",), group="Remote changes"),
    Command("git unstage", "git", "Remove a file from staging without discarding its edits",
            "Changes the index using reset HEAD --; working-tree edits remain. Requires HEAD.",
            ("git unstage README.md",), True, "Working changes"),
    Command("git amend-date", "git", "Change a commit's author and committer dates",
            "Previews dates and asks [y/N]; warns when earlier than a parent or found in local remote-tracking refs. Preserves staged changes and commit content. Older commits rewrite descendants on the current branch; merges and signed rewrites are refused. No commit hooks run.",
            ("git amend-date 1d", "git amend-date 2h", "git amend-date --shift 1d",
             "git amend-date 1d HEAD~1",
             'git amend-date "2026-10-30T12:00:00+01:00"'), True, "Working changes"),
    Command("git co", "git", "Check out a branch or restore paths",
            "Alias for checkout. Path arguments can discard unstaged edits; use git switch for branch-only intent.",
            ("git co feature-branch",), True, "Working changes"),
    Command("git br", "git", "List or manage branches",
            "Alias for branch; listing is read-only, creation/deletion flags change refs.",
            ("git br", "git br -vv"), True, "Working changes"),
    Command("git stats", "git", "Count non-merge commits per author",
            "Read-only; includes HEAD and accepts log filters.",
            ("git stats --since='6 months ago'",), group="Repository insights"),
    Command("git churn", "git", "Find files changed most often in the last year",
            "Read-only; counts path appearances in history, not changed lines.",
            ("git churn",), group="Repository insights"),
    Command("git bugspots", "git", "Find paths often touched by fix-related commits",
            "Read-only heuristic based on commit messages, not proof of defects.",
            ("git bugspots",), group="Repository insights"),
    Command("git pulse", "git", "Count commits per month",
            "Read-only; counts are not a measure of developer productivity.",
            ("git pulse",), group="Repository insights"),
    Command("Ctrl-R", "zsh", "Fuzzy-search command history",
            "fzf binding; selection puts a command on the line. Inspect it before Enter.",
            ("Press Ctrl-R, type part of a previous command, select with Enter.",), group="History and completion"),
    Command("Up/Down", "zsh", "Search history for text already typed",
            "Configured history-substring-search bindings; searches anywhere in the command.",
            ("Type pmu, then press Up or Down.",), group="History and completion"),
    Command("Tab", "zsh", "Complete commands, paths and supported flags",
            "fzf-tab provides the picker. Dotfiles command flags come from safe --help output.",
            ("Type dotfiles-help git, add a space, then press Tab.",), group="History and completion"),
    Command("Ctrl-T", "zsh", "Find a file and insert its path",
            "fzf file picker; when rg is installed it includes hidden files but skips .git and respects ignore rules.",
            ("Type a command followed by a space, then press Ctrl-T.",), group="Files and directories"),
    Command("Alt-C", "zsh", "Fuzzy-find and enter a directory",
            "fzf directory picker; this config also binds the character emitted by Option-C (ç). Terminal key settings may differ.",
            ("Press Alt-C (Option-C on many Mac layouts), then choose a directory.",), True, "Files and directories"),
    Command("leading-space", "zsh", "Keep a command out of saved shell history",
            "HIST_IGNORE_SPACE is enabled. Not a secrecy guarantee: arguments may still appear in process lists and logs.",
            ("Start the command line with a space.",), group="History and completion"),
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, f"Error: {message}\n")


def command_info(name):
    return next(command for command in COMMANDS if command.name == name)


def description(name):
    command = command_info(name)
    return f"{command.purpose}.\n\n{command.caution}"


def examples(name):
    command = command_info(name)
    entries = [example for example in command.examples if example != command.name]
    return "Examples:\n" + "\n".join(f"  {example}" for example in entries) if entries else ""


def help_epilog(name, extra=""):
    return "\n\n".join(part for part in (extra, examples(name)) if part)


def detail(command, width):
    output.row("warn" if command.changes else "info", output.paint(command.name, "bold"), "")
    for line in textwrap.wrap(command.purpose, width=width):
        output.note(line)
    for line in textwrap.wrap(command.caution, width=width):
        output.note(output.paint(line, "dim"))
    for example in command.examples:
        if example == command.name:
            continue
        prefix = "" if command.topic == "zsh" else "$ "
        for line in textwrap.wrap(prefix + example, width=width, subsequent_indent="  "):
            output.note(output.paint(line, "cyan"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--render-help":
        template = sys.stdin.read()
        print(template.replace("{summary}", description(argv[1]))
              .replace("{examples}", examples(argv[1])).rstrip())
        return 0
    if "-h" in argv or "--help" in argv:
        name = "pmh" if "--packages" in argv else "dotfiles-help"
        print(HELP.replace("{summary}", description(name)).replace("{examples}", examples(name)), end="")
        return 0
    parser = ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--packages", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("query", nargs="*")
    args = parser.parse_args(argv)
    topic = "packages" if args.packages else None
    query = list(args.query)
    if query and query[0] in TOPICS:
        selected = query.pop(0)
        if topic and topic != selected:
            print("Error: pmh/--packages only covers the packages topic.", file=sys.stderr)
            return 2
        topic = selected
    commands = [command for command in COMMANDS if topic is None or command.topic == topic]
    if query:
        names = {" ".join(query), f"{topic} {' '.join(query)}"}
        exact = [command for command in commands if command.name in names]
        commands = exact or [
            command for command in commands
            if all(word.casefold() in " ".join((
                command.name, command.topic, command.purpose, command.caution, *command.examples,
            )).casefold() for word in query)
        ]
    if not commands:
        if args.list:
            return 0
        print(f"Error: no commands match {' '.join(args.query)!r}. Try dotfiles-help.", file=sys.stderr)
        return 2
    if args.list:
        if topic is None and not query:
            for name, topic_description in TOPICS.items():
                print(f"{name}:{topic_description}")
        for command in commands:
            name = command.name.removeprefix("git ") if topic == "git" else command.name
            print(f"{name}:{command.purpose}")
        return 0
    detailed = bool(topic or query)
    output.context("Read-only reference", "examples are not executed")
    width = max(20, min(88, shutil.get_terminal_size().columns) - 4)
    for name, topic_description in TOPICS.items():
        grouped = [command for command in commands if command.topic == name]
        if not grouped:
            continue
        if not detailed:
            if name in ("git", "zsh"):
                continue
            output.section(f"{name} - {topic_description}")
            for command in grouped:
                if not command.group:
                    output.row("warn" if command.changes else "info", command.name, command.purpose)
            if any(command.group for command in grouped):
                output.row("info", f"dotfiles-help {name}", "Open the full grouped reference")
            if name == "shell":
                output.row("info", "dotfiles-help git", TOPICS["git"])
                output.row("info", "dotfiles-help zsh", TOPICS["zsh"])
            continue
        groups = dict.fromkeys(command.group for command in grouped)
        for group in groups:
            output.section(f"{name} / {group}" if group else f"{name} - {topic_description}")
            entries = [command for command in grouped if command.group == group]
            for index, command in enumerate(entries):
                if index:
                    print()
                detail(command, width)
    if not detailed:
        output.section("Help")
        output.row("info", "dotfiles-help <name>", "Examples and cautions for a command")
        output.row("info", "dotfiles-help <words>", "Search by task, e.g. disk space")
        output.row("info", "<command> --help", "Full options for script commands; Tab completes topics and names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
