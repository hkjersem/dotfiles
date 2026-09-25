#!/usr/bin/env bash
# Sourced after machine-profile.sh; standard Homebrew tools need no entry here.

_update_nav_pilot() {
    # Homebrew installations were upgraded by the caller's bundle/upgrade step.
    if [[ "$1" == false ]]; then
        nav-pilot upgrade || { echo "Error: Nav Pilot CLI upgrade failed." >&2; return 1; }
    fi
    nav-pilot sync --apply || { echo "Error: Nav Pilot sync failed." >&2; return 1; }
}

_update_gcloud() {
    if [[ "$1" == true ]]; then
        # This cask is marked auto_updates, so regular brew upgrade skips it.
        brew upgrade --cask --greedy gcloud-cli \
            || { echo "Error: Homebrew gcloud upgrade failed." >&2; return 1; }
    else
        gcloud components update --quiet \
            || { echo "Error: gcloud component update failed." >&2; return 1; }
    fi
}

update_installed_tool() {
    local tool="$1" heading="${2:-}" label executable resolved prefix
    local brew_managed=false required_on_work=false
    case "$tool" in
        nav-pilot) label="Nav Pilot"; required_on_work=true ;;
        gcloud) label="gcloud"; required_on_work=true ;;
        claude) label="Claude Code" ;;
        copilot) label="GitHub Copilot CLI" ;;
        codex) label="Codex" ;;
        *) echo "Error: no custom updater for $tool." >&2; return 1 ;;
    esac

    if ! executable=$(command -v "$tool"); then
        if [[ "$DOTFILES_PROFILE" == work && "$required_on_work" == true ]]; then
            echo "Error: $label is missing after work-profile setup. Check the Homebrew installation." >&2
            return 1
        fi
        return 0
    fi

    resolved=$(realpath "$executable") || return 1
    if command -v brew >/dev/null; then
        prefix=$(brew --prefix) || return 1
        case "$resolved" in
            "$prefix/Cellar/"*|"$prefix/Caskroom/"*|"$prefix/share/google-cloud-sdk/"*)
                brew_managed=true
                ;;
        esac
    fi

    case "$tool" in
        claude|codex) [[ "$brew_managed" == true ]] && return 0 ;;
    esac

    if [[ -n "$heading" ]]; then
        "$heading" "$label" || return 1
    else
        printf '%s\n' "$label"
    fi
    case "$tool" in
        nav-pilot) _update_nav_pilot "$brew_managed" ;;
        gcloud) _update_gcloud "$brew_managed" ;;
        claude|copilot|codex)
            "$tool" update || { echo "Error: $label update failed." >&2; return 1; }
            ;;
    esac
}

update_profile_tools() {
    local tool
    for tool in nav-pilot gcloud; do
        update_installed_tool "$tool" "${1:-}" || return 1
    done
}
