#!/usr/bin/env bash

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            cat <<'EOF'
Usage: dotfiles-install [options]

Reapply dotfiles links and bootstrap tools, then audit.
Leaves macOS preferences unchanged; use dotfiles-defaults to apply them.

  --no-defaults  Leave macOS preferences unchanged (already the default)
  -h, --help     Show this help
EOF
            exit 0
            ;;
        --no-defaults) ;;
        *) echo "Error: unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

bash "$HOME/.dotfiles/scripts/machine-profile.sh" &&
    bash "$HOME/.dotfiles/install" --no-audit &&
    bash "$HOME/.dotfiles/macos/install.sh" --no-defaults
