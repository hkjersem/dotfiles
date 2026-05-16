#!/usr/bin/env bash
#
# Delete all known build artifacts and dependency folders from the current directory.
# Usage: wipe_clean

set -euo pipefail

targets=(
    node_modules dist .dist build .build out .out coverage .coverage
    .next .nuxt .turbo .cache .parcel-cache .vite .svelte-kit
    .docusaurus storybook-static
    __pycache__ .venv venv
)

# Build exclusions: .git and any git submodule paths
exclude_expr=(-not -path "*/.git/*")
if [[ -f .gitmodules ]]; then
    while IFS= read -r submodule_path; do
        exclude_expr+=(-not -path "./$submodule_path" -not -path "./$submodule_path/*")
    done < <(git config --file .gitmodules --get-regexp 'submodule\..*\.path' | awk '{print $2}')
fi

find_expr=()
for t in "${targets[@]}"; do
    [[ ${#find_expr[@]} -gt 0 ]] && find_expr+=(-o)
    find_expr+=(-name "$t")
done

found=$(find . "${exclude_expr[@]}" \( "${find_expr[@]}" \) -type d -prune 2>/dev/null)

if [[ -z "$found" ]]; then
    echo "Nothing to clean."
    exit 0
fi

echo "Will delete:"
echo "$found" | sed 's/^/  /'
echo
printf "Proceed? [y/N] "
read -r -n 1 confirm
echo
case "$confirm" in
    $'\e')          echo "Aborted."; exit 0 ;;
    [Nn])           echo "Aborted."; exit 0 ;;
    [Yy]|"")        ;;
    *)              echo "Aborted."; exit 0 ;;
esac

while IFS= read -r dir; do
    rm -rf "$dir"
done <<< "$found"
echo "Done."
