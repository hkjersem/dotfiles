#!/usr/bin/env bash
# Shared terminal output style for this repository's scripts. scripts/lib/output.py mirrors it for Python,
# and scripts/tests/test_output.py checks that both print the same bytes.
#
#   out_section "Name"      blank line, then a bold "── Name ───…" heading
#   out_subsection "Name"   blank line, then a bold "  ▸ Name" heading under a section
#   "  $OUT_OK text"        status lines: ✓ done or passed, ! warning, ✗ failed, • info entry in a list
#   "  $ERR_FAIL text" >&2  the same marks for stderr: ERR_WARN, ERR_FAIL
#   "    ${DIM}text${RESET}"  detail under a status line, aligned with its text
#   "    text"              a standalone note, such as a verdict or "Dry run", aligned without a mark
#
# Colours apply only when stdout is a terminal and NO_COLOR is unset, so pipes and logs stay plain;
# the symbols carry the meaning without colour.

# shellcheck disable=SC2034  # the colours and marks are used by the scripts that source this file
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'
  YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
fi

OUT_OK="${GREEN}✓${RESET}"
OUT_WARN="${YELLOW}!${RESET}"
OUT_FAIL="${RED}✗${RESET}"
OUT_INFO="${DIM}•${RESET}"

# Marks for messages written to stderr, coloured only when stderr itself is a terminal
if [[ -t 2 && -z "${NO_COLOR:-}" ]]; then
  ERR_WARN=$'\033[1;33m!\033[0m'; ERR_FAIL=$'\033[0;31m✗\033[0m'
else
  ERR_WARN="!"; ERR_FAIL="✗"
fi

out_section() {
  printf '\n%s── %s %s%s\n' "$BOLD" "$1" "──────────────────────────────────" "$RESET"
}

out_subsection() {
  printf '\n%s  ▸ %s%s\n' "$BOLD" "$1" "$RESET"
}
