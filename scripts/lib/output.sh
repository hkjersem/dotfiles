#!/usr/bin/env bash
# Shared terminal output style for this repository's scripts. scripts/lib/output.py mirrors it for Python,
# and scripts/tests/test_output.py checks that both print the same bytes.
#
# Headings fill a fixed width (OUT_WIDTH) with their rule, so they line up whatever the title length.
#   out_title "Name"        bold "━ Name ━━━…" heading for a report run inside another
#   out_dry_run ["hint"]   first line of a dry run; add how to apply only when dry run is the default
#   out_context "a" "b"     a dim "a │ b" line of context, such as the target, at the top of a report
#   out_progress "text…"    a transient progress line on stderr, only on a terminal; out_progress_done clears it
#                          mirrored by output.progress() and output.progress_done() in Python
#   out_section "Name"      blank line, then a bold "─ Name ───…" heading
#   out_subsection "Name"   blank line, then a bold "  ▸ Name" heading under a section
# Columns: whole-run lines (dry run, context, headings, progress) start in column 0. Entries line up with
# their heading's text: column 2 under a section, column 4 under a subsection. Details go two further in.
#
#   "  $OUT_OK text"        status lines: ✓ done or passed, ! warning, ✗ failed, • info entry in a list
#   "  $ERR_FAIL text" >&2  the same marks for stderr: ERR_WARN, ERR_FAIL
#   "    ${DIM}text${RESET}"  detail under a status line, aligned with its text
#   out_note "text"         a standalone note, such as a verdict, aligned without a mark
#   out_row "$OUT_OK" "Label" "detail"   a status line with a label padded to a column
#   out_table_header "%-20s  %s" "A" "B"   a bold table header row; use the same format for the rows
#   out_summary ISSUES WARNINGS "All good"   the Summary section of an audit: counts, or the all-good line
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

OUT_WIDTH=60

# Prints TEXT followed by a rule of CHAR up to OUT_WIDTH characters, at least three.
_out_ruled() {
  # Count characters, not bytes, whatever the caller's locale.
  local LC_ALL=en_US.UTF-8
  local text=$1 char=$2 count rule
  count=$((OUT_WIDTH - ${#text}))
  [[ $count -lt 3 ]] && count=3
  printf -v rule '%*s' "$count" ''
  printf '%s%s%s%s\n' "$BOLD" "$text" "${rule// /$char}" "$RESET"
}

out_section() {
  echo ""
  _out_ruled "─ $1 " "─"
}

out_subsection() {
  printf '\n%s  ▸ %s%s\n' "$BOLD" "$1" "$RESET"
}

out_title() {
  _out_ruled "━ $1 " "━"
}

out_context() {
  local text=$1
  shift
  local part
  for part in "$@"; do text+=" │ $part"; done
  printf '%s%s%s\n' "$DIM" "$text" "$RESET"
}

out_dry_run() {
  printf '%sDry run%s — nothing will be changed.%s\n' "$YELLOW" "$RESET" "${1:+ $1}"
}

out_progress() {
  [[ -t 2 ]] && printf '\r\033[K%s%s%s' "$DIM" "$1" "$RESET" >&2
  return 0
}

out_progress_done() {
  [[ -t 2 ]] && printf '\r\033[K' >&2
  return 0
}

out_note() {
  printf '    %s\n' "$1"
}

out_listing_name() {
  local LC_ALL=C
  local text=$1 kind=$2 palette=${LSCOLORS:-Exfxcxdxbxegedabagacad} pair codes="" char index base
  if [[ ! -t 1 || -n "${NO_COLOR:-}" || ( "$kind" != folder && "$kind" != symlink ) ]]; then
    printf '%s' "$text"
    return
  fi
  if [[ "$kind" == folder ]]; then pair=${palette:0:2}; else pair=${palette:2:2}; fi
  if [[ ${#pair} -ne 2 || "$pair" == *[!abcdefghABCDEFGHx]* ]]; then
    printf '%s' "$text"
    return
  fi
  [[ "$pair" == *[A-H]* ]] && codes=1
  for index in 0 1; do
    char=${pair:index:1}
    [[ "$char" == x ]] && continue
    case "$char" in
      a|A) base=0 ;; b|B) base=1 ;; c|C) base=2 ;; d|D) base=3 ;;
      e|E) base=4 ;; f|F) base=5 ;; g|G) base=6 ;; h|H) base=7 ;;
    esac
    base=$((30 + 10 * index + base))
    codes="${codes:+$codes;}$base"
  done
  if [[ -n "$codes" ]]; then printf '\033[%sm%s\033[0m' "$codes" "$text"; else printf '%s' "$text"; fi
}

out_row() {
  printf '  %s %-22s %s\n' "$1" "$2" "$3"
}

out_table_header() {
  local format=$1
  shift
  # shellcheck disable=SC2059  # the caller passes the column format shared with its rows
  printf "  ${BOLD}${format}${RESET}\n" "$@"
}

out_summary() {
  out_section "Summary"
  if [[ "$1" -eq 0 && "$2" -eq 0 ]]; then
    printf '  %s %s\n' "$OUT_OK" "$3"
    return
  fi
  [[ "$1" -gt 0 ]] && printf '  %s %s issue(s) found\n' "$OUT_FAIL" "$1"
  [[ "$2" -gt 0 ]] && printf '  %s %s warning(s) found\n' "$OUT_WARN" "$2"
  return 0
}
