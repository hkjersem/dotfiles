#!/usr/bin/env bash
# Update installed software, then audit. Run as dotfiles-update, which passes --no-defaults.

SKIP_DEFAULTS=false
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      cat <<'EOF'
Usage: dotfiles-update

Updates macOS and App Store apps, Command Line Tools, Homebrew packages, Node
and npm globals, Zsh plugins and supported installed CLIs, then runs the audit.
It does not pull this repository; run git pull in ~/.dotfiles for that.

Options:
  --no-defaults  Skip applying macOS defaults (osxdefaults.sh); dotfiles-update always passes it
  -h, --help     Show this help
EOF
      exit 0
      ;;
    --no-defaults) SKIP_DEFAULTS=true ;;
    *) echo "Error: unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/machine-profile.sh" || exit 1
ensure_machine_profile || exit 1
source "$HOME/.dotfiles/scripts/lib/tool-updates.sh" || exit 1
source "$HOME/.dotfiles/scripts/lib/command-line-tools.sh" || exit 1
source "$HOME/.dotfiles/scripts/lib/output.sh" || exit 1

header() { out_section "$1"; }
subheader() { out_subsection "$1"; }

header "System & App Store"
# Update macOS
UPDATE_OUT=$(softwareupdate -i -a 2>&1)
echo "$UPDATE_OUT"
if echo "$UPDATE_OUT" | grep -qi "restart"; then
    read -r -p "A restart is required to complete installation. Restart now? [y/N] " _response
    if [[ "$_response" =~ ^[Yy]$ ]]; then
        sudo -n true 2>/dev/null && echo "Restarting..." || { echo "Administrator access required to restart. Please enter your password:"; sudo -v; echo "Restarting..."; }
        sudo shutdown -r now
    fi
fi
# Update App Store apps
command -v mas &>/dev/null && mas upgrade

header "Node"
# Update node — install latest LTS, migrate globals if version changed
bash ~/.dotfiles/scripts/install-node.sh lts
# install-node.sh may uninstall the version this shell's fnm symlink still
# points at, leaving node/npm/npx/pnpm unresolved until the next `cd`.
# Re-point this shell to the (possibly new) default now.
command -v fnm &>/dev/null && fnm use --install-if-missing lts-latest >/dev/null
bash ~/.dotfiles/scripts/ensure-pm-config.sh || exit 1
npm cache verify -g > /dev/null
# Other Node majors, then npm and global packages in every installation
bash ~/.dotfiles/scripts/update-node-versions.sh || exit 1
bash ~/.dotfiles/scripts/corepack-defaults.sh || exit 1

header "Homebrew"
# Update Homebrew (Cask) & packages
brew analytics off
brew update --quiet
# After brew update, so Homebrew knows the latest Command Line Tools release.
update_command_line_tools || exit 1
HOMEBREW_NO_ASK=1 brew bundle install --file="$HOME/.dotfiles/Brewfile" --quiet || exit 1
brew upgrade --yes || exit 1
brew cleanup

header "Zsh"
# Update Zsh plugins
ZSH=~/.oh-my-zsh DISABLE_UPDATE_PROMPT=true zsh ~/.oh-my-zsh/tools/upgrade.sh -v minimal -c 3
bash ~/.dotfiles/scripts/install-zsh-plugins.sh || exit 1

# Run settings script (skip with --no-defaults)
if [[ "$SKIP_DEFAULTS" == false ]]; then
    header "macOS defaults"
    bash ~/.dotfiles/macos/osxdefaults.sh
fi

header "Tools & integrations"
update_installed_tool claude subheader || exit 1
update_installed_tool copilot subheader || exit 1
update_installed_tool codex subheader || exit 1

subheader "Agent instructions"
bash ~/.dotfiles/scripts/sync-agent-instructions.sh --quiet || exit 1

if [ -d ~/.dotfiles/agents/skills ];
then
    subheader "Skills"
    bash ~/.dotfiles/scripts/sync-agent-skills.sh --quiet || exit 1
    npx -y skills update -g || exit 1
    bash ~/.dotfiles/scripts/audit-skills.sh --fix-symlinks --fix-lockfile --quiet || exit 1
fi
update_profile_tools subheader || exit 1

header "Cleanup"
# Cleanup
rm -rf ~/.npm/_npx
pnpm store prune

# End script
echo ""
echo "Done. Enjoy your updated install."
echo ""
out_title "Audit"
bash ~/.dotfiles/scripts/audit.sh
