#!/usr/bin/env bash
# Generates/updates the managed section of ~/.gitconfig_local and writes
# ~/.gitconfig_local_work and ~/.gitconfig_local_personal.
# Content outside the managed markers in ~/.gitconfig_local is preserved.
# Called during install. Safe to re-run — prompts before overwriting.

OUTPUT="$HOME/.gitconfig_local"
OUTPUT_WORK="$HOME/.gitconfig_local_work"
OUTPUT_PERSONAL="$HOME/.gitconfig_local_personal"
MARKER_BEGIN="# --- BEGIN setup-git-local.sh ---"
MARKER_END="# --- END setup-git-local.sh ---"

die() { echo "Error: $*" >&2; exit 1; }

read_answer() {
    IFS= read -r -p "$1" "$2" || die "Input cancelled; no Git configuration was changed."
}

confirm() {
    local answer
    while true; do
        read_answer "$1" answer
        case "$answer" in
            y|Y) return 0 ;;
            n|N|"") return 1 ;;
            *) echo "Please enter y or n." >&2 ;;
        esac
    done
}

normalize_directory() {
    local directory="$1"
    case "$directory" in
        '~') directory="$HOME" ;;
        '~/'*) directory="$HOME/${directory#\~/}" ;;
        /*) ;;
        *) echo "Error: use an absolute directory or a path starting with ~/." >&2; return 1 ;;
    esac
    while [[ "$directory" != / && "$directory" == */ ]]; do
        directory="${directory%/}"
    done
    printf '%s\n' "$directory"
}

personal_email="$(git config --global user.email)" \
    || die "Set your personal identity with git config --global user.email first."
personal_name="$(git config --global user.name)" \
    || die "Set your personal identity with git config --global user.name first."
[[ -n "$personal_email" && -n "$personal_name" ]] || die "The global Git name and email must not be empty."

for file in "$OUTPUT" "$OUTPUT_WORK" "$OUTPUT_PERSONAL"; do
    if [[ ( -e "$file" || -L "$file" ) && ! -f "$file" ]]; then
        die "$file is not a regular file."
    fi
done

echo ""
echo "Git local config setup"
echo "----------------------"
echo "The global default is $personal_name <$personal_email> (from your global Git configuration)."
echo "Use this to configure directory-based overrides for this machine."
echo ""

if [[ -f "$OUTPUT" ]]; then
    echo "$OUTPUT already exists:"
    echo ""
    cat "$OUTPUT" || die "Could not read $OUTPUT."
    echo ""
    confirm "Reconfigure? [y/N] " || { echo "Skipping."; exit 0; }
    echo ""
fi

is_work=false
exceptions=()
if confirm "Configure a work identity for a directory? [y/N] "; then
    is_work=true
    read_answer "Work directory [~/Developer]: " work_dir
    work_dir="$(normalize_directory "${work_dir:-~/Developer}")" || exit 1

    read_answer "Work email: " work_email
    [[ -n "$work_email" ]] || die "Work email must not be empty."
    read_answer "Work name [$personal_name]: " work_name
    work_name="${work_name:-$personal_name}"

    # Collect personal exception directories within the work dir
    echo ""
    echo "Add personal repo exceptions inside $work_dir (leave blank to finish):"
    while true; do
        read_answer "  Exception path (e.g. ~/Developer/personal-project): " exc
        [[ -z "$exc" ]] && break
        exc="$(normalize_directory "$exc")" || exit 1
        exceptions+=("$exc")
    done
fi

umask 077
temporary="$(mktemp -d "$HOME/.gitconfig-setup.XXXXXX")" || die "Could not create temporary configuration files."
trap 'rm -f "$temporary/work" "$temporary/personal" "$temporary/block" "$temporary/local"; rmdir "$temporary"' EXIT
block="$temporary/block"
printf '%s\n' "$MARKER_BEGIN" > "$block" || die "Could not write the managed block."

if [[ "$is_work" == true ]]; then
    git config --file "$temporary/work" user.name "$work_name" \
        && git config --file "$temporary/work" user.email "$work_email" \
        && git config --file "$temporary/personal" user.name "$personal_name" \
        && git config --file "$temporary/personal" user.email "$personal_email" \
        || die "Could not generate Git identities."
    git config --file "$block" "includeIf.gitdir:${work_dir%/}/.path" "$OUTPUT_WORK" \
        || die "Could not configure the work directory."
    for exc in "${exceptions[@]}"; do
        git config --file "$block" --add "includeIf.gitdir:${exc%/}/.path" "$OUTPUT_PERSONAL" \
            || die "Could not configure the personal exception."
    done
else
    printf '%s\n' "# No machine-specific git overrides" >> "$block" || die "Could not write the managed block."
fi
printf '%s\n' "$MARKER_END" >> "$block" || die "Could not finish the managed block."

# Read replacement lines from a file: macOS awk rejects multiline -v values.
if [[ -f "$OUTPUT" ]]; then
    awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
        FILENAME == ARGV[1] { block[++count]=$0; next }
        $0 == begin {
            if (inside || found) { bad=1; exit }
            found=1; inside=1
            for (i=1; i<=count; i++) print block[i]
            next
        }
        $0 == end {
            if (!inside) { bad=1; exit }
            inside=0
            next
        }
        !inside { print }
        END {
            if (bad || inside) exit 1
            if (!found) {
                print ""
                for (i=1; i<=count; i++) print block[i]
            }
        }
    ' "$block" "$OUTPUT" > "$temporary/local" \
        || die "Could not replace the managed block; check for missing or duplicate markers."
else
    cat "$block" > "$temporary/local" || die "Could not prepare $OUTPUT."
fi
git config --file "$temporary/local" --list >/dev/null || die "The resulting Git configuration is invalid."

if [[ "$is_work" == true ]]; then
    mv -f "$temporary/work" "$OUTPUT_WORK" \
        && mv -f "$temporary/personal" "$OUTPUT_PERSONAL" \
        || die "Could not save the Git identities."
fi
mv -f "$temporary/local" "$OUTPUT" || die "Could not save $OUTPUT."

echo ""
echo "Updated $OUTPUT:"
echo ""
cat "$OUTPUT" || die "Could not read $OUTPUT."
