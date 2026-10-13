#!/usr/bin/env bash
#
# Link the shared global agent instructions (agents/AGENTS.md) into each agent
# tool used on this machine. Tools whose home directory does not exist are
# skipped. Existing instruction files with content and symlinks to other
# locations are reported as conflicts and left unchanged.
#
# Usage: bash scripts/sync-agent-instructions.sh [--dry-run] [--quiet]

DRY_RUN=0
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --quiet) QUIET=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

source "$HOME/.dotfiles/scripts/lib/agent-instructions.sh" || exit 1

say() { [ "$QUIET" -eq 1 ] || echo "$1"; }

if [ ! -f "$AGENT_INSTRUCTIONS_SOURCE" ]; then
    echo "Agent instructions: source missing ($AGENT_INSTRUCTIONS_SOURCE)" >&2
    exit 1
fi

changes=0
conflicts=0
errors=0
for entry in "${AGENT_INSTRUCTION_TARGETS[@]}"; do
    label="${entry%%:*}"
    rest="${entry#*:}"
    tool_home="${rest%:*}"
    path="$tool_home/${rest##*:}"
    display="${path/#$HOME/~}"
    state=$(agent_instruction_state "$tool_home" "$path")

    case "$state" in
        absent) say "  skip: $label (not installed)" ;;
        linked) say "  ok: $label ($display)" ;;
        missing|empty|stale)
            if [ "$DRY_RUN" -eq 1 ]; then
                say "  would link: $label ($display)"
                changes=$((changes + 1))
            elif ln -sfn "$AGENT_INSTRUCTIONS_SOURCE" "$path"; then
                say "  linked: $label ($display)"
                changes=$((changes + 1))
            else
                echo "  error: $label could not link $display" >&2
                errors=$((errors + 1))
            fi
            ;;
        foreign|file)
            echo "  conflict: $label ($display exists; move its rules into agents/AGENTS.md, then remove it)" >&2
            conflicts=$((conflicts + 1))
            ;;
    esac
done

if [ "$QUIET" -eq 1 ] && [ "$conflicts" -eq 0 ] && [ "$errors" -eq 0 ]; then
    if [ "$changes" -eq 0 ]; then
        echo "Agent instructions: up to date"
    else
        echo "Agent instructions: $changes target(s) linked"
    fi
else
    echo "Agent instructions: $changes change(s), $conflicts conflict(s), $errors error(s)"
fi
[ "$errors" -eq 0 ]
