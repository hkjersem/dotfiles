#!/usr/bin/env bash

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            python3 "$(dirname "${BASH_SOURCE[0]}")/dotfiles-help.py" --render-help dotfiles-install <<'EOF'
Usage: dotfiles-install [options]

{summary}

  --no-defaults  Leave macOS preferences unchanged (already the default)
  -h, --help     Show this help

{examples}
EOF
            exit $?
            ;;
        --no-defaults) ;;
        *) echo "Error: unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

bash "$HOME/.dotfiles/scripts/machine-profile.sh" &&
    bash "$HOME/.dotfiles/install" --no-audit &&
    bash "$HOME/.dotfiles/macos/install.sh" --no-defaults
