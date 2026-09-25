#!/usr/bin/env bash

load_machine_profile() {
    local file="$HOME/.config/dotfiles/profile"
    DOTFILES_PROFILE=""
    export DOTFILES_PROFILE
    [[ -e "$file" || -L "$file" ]] || return 1
    if [[ ! -f "$file" ]] || ! DOTFILES_PROFILE=$(cat "$file"); then
        DOTFILES_PROFILE=""
        echo "Error: could not read $file." >&2
        return 2
    fi
    case "$DOTFILES_PROFILE" in
        personal|work) return 0 ;;
        *)
            DOTFILES_PROFILE=""
            echo "Error: invalid machine profile. Run dotfiles-profile personal or dotfiles-profile work." >&2
            return 2
            ;;
    esac
}

save_machine_profile() {
    case "${1:-}" in
        personal|work) ;;
        *) echo "Error: machine profile must be personal or work." >&2; return 2 ;;
    esac
    (
        umask 077
        directory="$HOME/.config/dotfiles"
        mkdir -p "$directory" || exit 1
        temporary=$(mktemp "$directory/.profile.XXXXXX") || exit 1
        trap 'rm -f "$temporary"' EXIT
        printf '%s\n' "$1" > "$temporary" && mv -f "$temporary" "$directory/profile"
    ) || { echo "Error: could not save the machine profile." >&2; return 1; }
    DOTFILES_PROFILE="$1"
    export DOTFILES_PROFILE
}

choose_machine_profile() {
    local answer
    if [[ ! -t 0 ]]; then
        echo "Error: set a machine profile first: bash ~/.dotfiles/scripts/machine-profile.sh personal (or work)." >&2
        return 1
    fi
    while true; do
        printf 'Is this a personal or work machine? [personal/work] ' >&2
        IFS= read -r answer || { echo "Error: no machine profile selected." >&2; return 1; }
        case "$answer" in
            personal|work) save_machine_profile "$answer"; return $? ;;
            *) echo "Please enter personal or work." >&2 ;;
        esac
    done
}

ensure_machine_profile() {
    local status
    if load_machine_profile; then
        return 0
    else
        status=$?
    fi
    [[ "$status" -eq 1 ]] || return "$status"
    choose_machine_profile
}

machine_profile_main() {
    if [[ $# -gt 1 ]]; then
        echo "Usage: dotfiles-profile [personal|work|--choose]" >&2
        return 2
    fi
    case "${1:-}" in
        "") ensure_machine_profile || return $? ;;
        personal|work) save_machine_profile "$1" || return $? ;;
        --choose) choose_machine_profile || return $? ;;
        --help|-h) echo "Usage: dotfiles-profile [personal|work|--choose]"; return 0 ;;
        *) echo "Usage: dotfiles-profile [personal|work|--choose]" >&2; return 2 ;;
    esac
    printf 'Machine profile: %s\n' "$DOTFILES_PROFILE"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    machine_profile_main "$@"
fi
