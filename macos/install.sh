#!/usr/bin/env bash
# Usage: install.sh [--no-defaults]
#   --no-defaults  Skip running osxdefaults.sh

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SKIP_DEFAULTS=false
for arg in "$@"; do
  [[ "$arg" == "--no-defaults" ]] && SKIP_DEFAULTS=true
done

# Run install scripts
[[ "$SKIP_DEFAULTS" == false ]] && source "${DOTFILES}/macos/osxdefaults.sh"
source "${DOTFILES}/macos/applications.sh"

# End script
echo "Done. Enjoy your fresh install."

bash "${DOTFILES}/scripts/audit.sh"
