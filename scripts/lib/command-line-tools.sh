#!/usr/bin/env bash
# Sourced by macos/update.sh and scripts/audit.sh.
# Detection reuses Homebrew's own doctor checks. Homebrew has no command that
# updates the tools, so updates list them through the same softwareupdate
# on-demand marker that Homebrew's installer uses.

CLT_DIR=/Library/Developer/CommandLineTools
CLT_ON_DEMAND_MARKER="${DOTFILES_CLT_MARKER:-/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress}"
CLT_REINSTALL_HINT="sudo rm -rf $CLT_DIR && sudo xcode-select --install"

# Prints a one-line finding and returns 0 when the tools are missing or outdated,
# 1 when they are current, and 2 when Homebrew cannot tell.
command_line_tools_outdated() {
    local output
    if ! xcode-select -p >/dev/null 2>&1; then
        echo "Command Line Tools are not installed."
        return 0
    fi
    command -v brew >/dev/null || return 2
    if output=$(HOMEBREW_NO_AUTO_UPDATE=1 brew doctor check_clt_minimum_version check_clt_up_to_date 2>&1); then
        return 1
    fi
    if printf '%s\n' "$output" | grep -q 'Command Line Tools'; then
        # brew doctor prints a disclaimer first and advice after the headline.
        printf '%s\n' "$output" | sed -n '/^Warning: /{s///p;q;}'
        return 0
    fi
    printf '%s\n' "$output" >&2
    return 2
}

# Newest Command Line Tools label offered by softwareupdate (the marker must exist).
_command_line_tools_label() {
    softwareupdate --list 2>/dev/null \
        | grep -E '^[[:space:]]*\*.*Command Line Tools' \
        | sed -e 's/^[[:space:]]*\*[[:space:]]*//' -e 's/^Label:[[:space:]]*//' \
        | sort -V | tail -n 1
}

update_command_line_tools() {
    local finding status label installed=false
    finding=$(command_line_tools_outdated)
    status=$?
    if [[ $status -eq 1 ]]; then
        echo "Command Line Tools are up to date."
        return 0
    fi
    if [[ $status -eq 2 ]]; then
        echo "Warning: Homebrew could not check the Command Line Tools; skipped their update." >&2
        return 0
    fi
    printf '%s\n' "$finding"

    if ! touch "$CLT_ON_DEMAND_MARKER"; then
        echo "Error: could not create $CLT_ON_DEMAND_MARKER." >&2
        return 1
    fi
    label=$(_command_line_tools_label)
    if [[ -n "$label" ]]; then
        echo "Installing $label..."
        # --verbose shows download progress.
        sudo softwareupdate --install --verbose "$label" && installed=true
    fi
    rm -f "$CLT_ON_DEMAND_MARKER"

    if [[ "$installed" == true ]] && ! xcode-select -p >/dev/null 2>&1; then
        sudo xcode-select --switch "$CLT_DIR" || installed=false
    fi
    if [[ "$installed" == true ]] && command_line_tools_outdated >/dev/null 2>&1; then
        installed=false
    fi
    if [[ "$installed" == false ]]; then
        if [[ -z "$label" ]]; then
            echo "Warning: Software Update offers no Command Line Tools update." >&2
        else
            echo "Warning: the Command Line Tools are still missing or outdated." >&2
        fi
        echo "Reinstall them with: $CLT_REINSTALL_HINT" >&2
    fi
    return 0
}
