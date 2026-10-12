#!/usr/bin/env bash
#
# Shared global agent instruction targets and state detection.
# Sourced by scripts/sync-agent-instructions.sh and scripts/audit.sh.

AGENT_INSTRUCTIONS_SOURCE="$HOME/.dotfiles/agents/AGENTS.md"

# Format: "label:tool-home:instruction-file-name". A target is only managed
# when its tool home exists, meaning the tool is used on this machine.
AGENT_INSTRUCTION_TARGETS=(
    "Claude Code:$HOME/.claude:CLAUDE.md"
    "Codex:$HOME/.codex:AGENTS.md"
    "Copilot CLI:$HOME/.copilot:copilot-instructions.md"
)

# Prints one of:
#   absent    tool home does not exist
#   linked    symlink to the shared file
#   missing   no file
#   empty     empty regular file, safe to replace
#   stale     dangling symlink or symlink into the dotfiles repository
#   foreign   symlink pointing somewhere else
#   file      regular file with content, or another kind of path
agent_instruction_state() {
    local tool_home="$1" path="$2" link
    [ -d "$tool_home" ] || { echo absent; return; }
    if [ -L "$path" ]; then
        link=$(readlink "$path")
        if [ "$link" = "$AGENT_INSTRUCTIONS_SOURCE" ]; then
            echo linked
        elif [ ! -e "$path" ] || [[ "$link" == "$HOME/.dotfiles/"* ]]; then
            echo stale
        else
            echo foreign
        fi
    elif [ ! -e "$path" ]; then
        echo missing
    elif [ -f "$path" ] && [ ! -s "$path" ]; then
        echo empty
    else
        echo file
    fi
}
