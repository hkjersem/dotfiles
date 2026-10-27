#!/usr/bin/env python3
"""Maintain a roadmap index generated from per-item briefs.

Commands (run from the repository root, or pass --root):
  init            Create the index, brief directory and archive when absent.
  sync            Regenerate the marked block of the index from brief front matter.
  check           Report problems without writing; exit 1 when errors are found.
                  --strict adds heuristic checks (code pointers, backlinks, sections).
  ready           Show what is due, in progress, and which committed items can start.
  archive SLUG    Move a done or dropped brief to the archive, fix its relative
                  links, then sync.

Requires Python 3.9+ and the standard library only. Exits 2 without changes when
the roadmap uses a different format.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple, Union

START = "<!-- roadmap:start -->"
END = "<!-- roadmap:end -->"

STATUSES = ["idea", "planned", "ready", "active", "verifying", "done", "dropped"]
OPEN = {"idea", "planned", "ready", "active", "verifying"}
CLOSED = {"done", "dropped"}
KINDS = ["feature", "debt", "bug", "investigation", "routine"]
KEYS = ["title", "status", "summary", "kind", "depends_on", "blocked", "when",
        "due", "every", "last_done"]
LIST_KEYS = {"depends_on"}
ROUTINE_STATUSES = {"idea", "planned", "ready", "active", "dropped"}
EVERY_RE = re.compile(r"^([1-9][0-9]*)([dwm])$")
DUE_SOON_DAYS = 7

INDEX_TEMPLATE = """# Roadmap

<!-- One short paragraph: what this project is for and what the roadmap optimises for. -->

## How this roadmap works

- Each item has a brief in [{dir}/]({dir}/). Its front matter (`title`, `status`,
  `summary`, optional `kind`, `depends_on`, `blocked`, `when`, `due`) is the only
  record of status. To work on an item, read its brief; this file is only the map.
- Statuses: idea, planned (committed, decisions open), ready (fully specified),
  active (being implemented), verifying (built; checks or acceptance outstanding),
  done, dropped. Being listed, even as ready, does not authorise implementation.
- The block between the roadmap markers is generated; change briefs, not its
  lines. Now: active and verifying. Next: planned and ready items without a
  `when` condition, unordered. Later: ideas, and committed items the owner
  parked with `when`. Real sequencing is `depends_on`; there is no ranking.
  Routines (`kind: routine`) recur `every` interval and never finish: doing one
  means a dated line in its `## Log`, a new `last_done` and removing any `due`
  (which overrides the computed date). `due` is a target date; nothing runs by
  itself, `ready` lists what is due.
- The owner decides commitment, readiness, parking, acceptance and dropping, and
  assigns work; agents propose. `active` reports who is working on an item; it
  is not a lock, so ask before taking over an active item.
- Done and dropped briefs move to [{dir}/archive/]({dir}/archive/) after the owner
  accepts them. Shipped behaviour belongs in the maintained docs, not in briefs.

{start}
{end}

## Ideas

<!-- One-liners not yet worth a brief: "- **Name**: why. When: entry condition." -->

## Not doing

<!-- Rejected directions and why, so they are not proposed again. -->
"""


class FrontMatterError(Exception):
    pass


class Brief:
    def __init__(self, path: str, slug: str, archived: bool):
        self.path = path
        self.slug = slug
        self.archived = archived
        self.meta: Dict[str, Union[str, List[str]]] = {}
        self.valid = False
        self.text = ""

    def get(self, key: str) -> str:
        value = self.meta.get(key, "")
        return value if isinstance(value, str) else ", ".join(value)

    @property
    def title(self) -> str:
        return self.get("title") or self.slug

    @property
    def status(self) -> str:
        return self.get("status")

    @property
    def depends_on(self) -> List[str]:
        value = self.meta.get("depends_on", [])
        return value if isinstance(value, list) else [value]

    @property
    def routine(self) -> bool:
        return self.get("kind") == "routine"

    def next_due(self) -> Optional[datetime.date]:
        """Explicit `due`, else last_done + every for routines. Invalid values
        give None; validate() reports them."""
        due = parse_date(self.get("due"))
        if due or not self.routine:
            return due
        last, step = parse_date(self.get("last_done")), parse_every(self.get("every"))
        return add_interval(last, step) if last and step else None


def parse_date(value: str) -> Optional[datetime.date]:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value or ""):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def parse_every(value: str) -> Optional[Tuple[int, str]]:
    match = EVERY_RE.match(value or "")
    return (int(match.group(1)), match.group(2)) if match else None


def add_interval(start: datetime.date, step: Tuple[int, str]) -> Optional[datetime.date]:
    """Completion-relative; month steps clamp to the month's last day, so a
    31st becomes the 28th in February and stays there. None when out of range."""
    count, unit = step
    try:
        if unit == "d":
            return start + datetime.timedelta(days=count)
        if unit == "w":
            return start + datetime.timedelta(weeks=count)
        month = start.month - 1 + count
        year, month = start.year + month // 12, month % 12 + 1
        return datetime.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    except (OverflowError, ValueError):
        return None


class Report:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def error(self, where: str, problem: str, fix: str) -> None:
        self.errors.append(f"{where}: {problem}\n    fix: {fix}")

    def warn(self, where: str, problem: str, fix: str) -> None:
        self.warnings.append(f"{where}: {problem}\n    fix: {fix}")


# --- Front matter -----------------------------------------------------------

def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        inner = value[1:-1]
        if re.search(r"(?<!')'(?!')", inner.replace("''", "")):
            raise FrontMatterError(f"unbalanced quote in {value!r}; write a literal ' as ''")
        return inner.replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        if "\\" in inner or '"' in inner:
            raise FrontMatterError(f"escapes are not supported in {value!r}; use single quotes")
        return inner
    if value[:1] in ("\"", "'", "|", ">", "{", "[", "&", "*", "!", "%", "@", "`"):
        raise FrontMatterError(f"unsupported value syntax: {value!r}")
    if re.search(r":\s|\s#", value):
        raise FrontMatterError(f"quote {value!r}; it contains ': ' or ' #'")
    return value


def parse_front_matter(text: str) -> Tuple[Dict[str, Union[str, List[str]]], str]:
    """Parse a deliberately small subset of YAML: one `key: value` per line,
    `depends_on` only as `[a, b]`, quotes as in YAML without escapes, full-line
    `#` comments. Anything else is rejected so that agents fix the syntax instead
    of the parser guessing."""
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontMatterError("no front matter (the file must start with ---)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FrontMatterError("front matter is not closed with ---")
    meta: Dict[str, Union[str, List[str]]] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([a-z_]+):(?:[ \t]+(.*))?$", line)
        if not match:
            raise FrontMatterError(
                f"unsupported line {line!r}; use one `key: value` per line and `key: [a, b]` for lists")
        key, raw = match.group(1), (match.group(2) or "").strip()
        if key in meta:
            raise FrontMatterError(f"duplicate key `{key}`")
        if key in LIST_KEYS:
            if not raw:
                meta[key] = []
                continue
            if not (raw.startswith("[") and raw.endswith("]")):
                raise FrontMatterError(f"`{key}` must be a one-line list: `{key}: [a, b]`")
            items = [unquote(p) for p in raw[1:-1].split(",")] if raw[1:-1].strip() else []
            if any(not item for item in items):
                raise FrontMatterError(f"`{key}` has an empty entry")
            meta[key] = items
        else:
            if raw.startswith("["):
                raise FrontMatterError(f"`{key}` takes a single value, not a list")
            meta[key] = unquote(raw) if raw else ""
    return meta, "\n".join(lines[end + 1:])


def load_briefs(brief_dir: str, report: Report, root: str, index_path: str = "") -> List[Brief]:
    briefs: List[Brief] = []
    for archived, directory in ((False, brief_dir), (True, os.path.join(brief_dir, "archive"))):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md") or name.lower() == "readme.md":
                continue
            path = os.path.join(directory, name)
            if index_path and os.path.normpath(path) == os.path.normpath(index_path):
                continue
            brief = Brief(path, name[:-3], archived)
            with open(path, encoding="utf-8") as handle:
                brief.text = handle.read()
            try:
                brief.meta, _ = parse_front_matter(brief.text)
                brief.valid = True
            except FrontMatterError as exc:
                report.error(rel(path, root), str(exc),
                             "use the restricted front matter shown in the brief template")
            briefs.append(brief)
    return briefs


# --- Rendering --------------------------------------------------------------

def rel(path: str, root: str) -> str:
    return os.path.relpath(path, root)


def posix_rel(target: str, start_dir: str) -> str:
    return os.path.relpath(target, start_dir).replace(os.sep, "/")


def entry(brief: Brief, index_path: str, done: Set[str]) -> str:
    parts = [f"[{brief.title}]({posix_rel(brief.path, os.path.dirname(index_path))})", brief.status]
    if brief.get("kind") and brief.get("kind") != "feature":
        parts.append(brief.get("kind"))
    if brief.get("summary"):
        parts.append(brief.get("summary"))
    pending = [d for d in brief.depends_on if d not in done]
    if pending:
        parts.append("after: " + ", ".join(pending))
    if brief.routine and brief.get("every"):
        parts.append("every " + brief.get("every"))
    if brief.get("due") or (brief.routine and brief.get("last_done")):
        due = brief.next_due()
        parts.append("due: " + (due.isoformat() if due else "invalid, see check"))
    elif brief.routine and brief.status != "idea":
        parts.append("due: not yet done")
    if brief.get("blocked"):
        parts.append("**blocked:** " + brief.get("blocked"))
    return " · ".join(p.replace("\n", " ") for p in parts)


def is_open(brief: Brief) -> bool:
    return brief.valid and not brief.archived and brief.status in OPEN


def horizon(brief: Brief) -> Optional[str]:
    if not is_open(brief):
        return None
    if brief.status == "idea" or brief.get("when"):
        return "later"
    if brief.routine:
        return "routines"
    if brief.status in ("active", "verifying"):
        return "now"
    return "next"


def plan(briefs: List[Brief]) -> Tuple[List[Brief], List[Brief], List[Brief], List[Brief]]:
    now = sorted((b for b in briefs if horizon(b) == "now"),
                 key=lambda b: (b.status != "active", b.title.lower()))
    nxt = sorted((b for b in briefs if horizon(b) == "next"),
                 key=lambda b: (b.status != "ready", b.title.lower()))
    routines = sorted((b for b in briefs if horizon(b) == "routines"), key=lambda b: b.title.lower())
    later = sorted((b for b in briefs if horizon(b) == "later"), key=lambda b: b.title.lower())
    return now, nxt, routines, later


def render(index_path: str, briefs: List[Brief]) -> str:
    now, nxt, routines, later = plan(briefs)
    done = {b.slug for b in briefs if b.valid and b.status == "done"}
    out = ["<!-- Generated from brief front matter by the roadmap skill;",
           "     edits here are overwritten on sync. -->", "", "## Now", ""]
    out += [f"- {entry(b, index_path, done)}" for b in now] or ["_Nothing in progress._"]
    out += ["", "## Next", ""]
    out += [f"- {entry(b, index_path, done)}" for b in nxt] or ["_Nothing committed._"]
    if routines:
        out += ["", "## Routines", ""]
        out += [f"- {entry(b, index_path, done)}" for b in routines]
    out += ["", "## Later", ""]
    out += [f"- {entry(b, index_path, done)}" + (f" · when: {b.get('when')}" if b.get("when") else "")
            for b in later] or ["_Nothing here._"]
    return "\n" + "\n".join(out) + "\n"


def split_index(text: str) -> Optional[Tuple[str, str, str]]:
    if text.count(START) != 1 or text.count(END) != 1:
        return None
    start, end = text.find(START), text.find(END)
    if end < start:
        return None
    return text[: start + len(START)], text[start + len(START): end], text[end:]


# --- Links ------------------------------------------------------------------

INLINE_LINK_RE = re.compile(
    r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^)\s]+)((?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^)\n]*\)))?\s*\))")
REF_DEF_RE = re.compile(r"(^[ ]{0,3}\[[^\]\n]+\]:[ \t]*)(<[^>\n]+>|\S+)", re.M)
FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
CODE_PATH_RE = re.compile(r"`((?:\.{1,2}/)?[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]+)`")
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "target", "__pycache__"}
# Hidden directories that hold repository-owned agent instructions; other
# hidden directories are tool state and are never rewritten.
AGENT_DIRS = {".github", ".agents", ".claude", ".codex", ".cursor", ".gemini", ".windsurf"}


def prose_segments(text: str) -> List[Tuple[bool, str]]:
    """Split text into (is_prose, segment) pieces, separating fenced blocks and
    inline code spans so link handling never touches code examples."""
    pieces: List[Tuple[bool, str]] = []
    fence: Optional[str] = None
    buffer: List[str] = []
    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if fence is None and match:
            pieces.append((True, "".join(buffer)))
            buffer, fence = [line], match.group(1)[0] * len(match.group(1))
        elif fence is not None and line.strip().startswith(fence):
            buffer.append(line)
            pieces.append((False, "".join(buffer)))
            buffer, fence = [], None
        else:
            buffer.append(line)
    pieces.append((fence is None, "".join(buffer)))
    result: List[Tuple[bool, str]] = []
    for is_prose, segment in pieces:
        if not is_prose:
            result.append((False, segment))
            continue
        for i, part in enumerate(re.split(r"(`[^`\n]*`)", segment)):
            result.append((i % 2 == 0, part))
    return result


def map_links(text: str, change) -> str:
    """Apply `change(target) -> target` to every link destination outside code."""
    def inline(match: "re.Match[str]") -> str:
        return match.group(1) + wrap(match.group(2), change) + match.group(3)

    def ref(match: "re.Match[str]") -> str:
        return match.group(1) + wrap(match.group(2), change)

    out = []
    for is_prose, segment in prose_segments(text):
        if is_prose:
            segment = REF_DEF_RE.sub(ref, INLINE_LINK_RE.sub(inline, segment))
        out.append(segment)
    return "".join(out)


def wrap(raw: str, change) -> str:
    if raw.startswith("<") and raw.endswith(">"):
        return "<" + change(raw[1:-1]) + ">"
    return change(raw)


def link_targets(text: str) -> List[str]:
    found: List[str] = []

    def collect(target: str) -> str:
        found.append(target)
        return target

    map_links(text, collect)
    return [t for t in found if is_local(t)]


def is_local(target: str) -> bool:
    return bool(target) and not target.startswith("#") and not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I)


def check_links(path: str, text: str, root: str, report: Report,
                directory_cache: Optional[Dict[str, List[str]]] = None) -> None:
    base = os.path.dirname(path)
    for target in link_targets(text):
        wanted = os.path.normpath(os.path.join(base, target.split("#", 1)[0]))
        actual = disk_case(wanted, root, directory_cache)
        if actual is None:
            report.error(rel(path, root), f"broken link to {target}",
                         "point it at the moved or renamed file, or remove it if the target is gone")
        elif actual != os.path.normpath(os.path.abspath(wanted)):
            report.error(rel(path, root), f"link to {target} differs in case from {rel(actual, root)}",
                         "match the casing on disk; the link breaks on case-sensitive systems")


def markdown_files(root: str) -> List[str]:
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and (not d.startswith(".") or d in AGENT_DIRS))
        found += [os.path.join(directory, f) for f in sorted(files) if f.endswith(".md")]
    return found


# --- Validation -------------------------------------------------------------

def validate(briefs: List[Brief], index_path: str, root: str,
             strict: bool, report: Report) -> None:
    slugs: Dict[str, Brief] = {}
    for brief in briefs:
        where = rel(brief.path, root)
        if brief.slug in slugs:
            report.error(where, f"slug also used by {rel(slugs[brief.slug].path, root)}",
                         "rename one; slugs are unique across the briefs and the archive")
        slugs[brief.slug] = brief
        if not brief.valid:
            continue
        for key in brief.meta:
            if key not in KEYS:
                report.error(where, f"unknown front matter key `{key}`",
                             f"use only {', '.join(KEYS)}; move other information into the body")
        if not brief.get("title"):
            report.error(where, "missing `title`", "add a short descriptive `title:`")
        status = brief.status
        if status not in STATUSES:
            report.error(where, f"status `{status or '(empty)'}` is not one of {', '.join(STATUSES)}",
                         "map it to the closest status; ask the owner when the mapping would change meaning")
            continue
        if brief.get("kind") and brief.get("kind") not in KINDS:
            report.error(where, f"kind `{brief.get('kind')}` is not one of {', '.join(KINDS)}",
                         "use the closest kind, or omit it for feature")
        for key in ("due", "last_done"):
            if key in brief.meta and not parse_date(brief.get(key)):
                report.error(where, f"`{key}` is not a valid YYYY-MM-DD date",
                             f"write it as an ISO date, for example `{key}: 2026-10-12`")
        if brief.routine:
            if not parse_every(brief.get("every")):
                report.error(where, "routine needs `every` as a number and d, w or m",
                             "for example `every: 2w`; use `due` alone for a one-off date")
            if status not in ROUTINE_STATUSES:
                report.error(where, f"routine has status `{status}`; routines never finish",
                             "record `last_done` and a `## Log` line instead, or drop it to stop it")
            due, last = parse_date(brief.get("due")), parse_date(brief.get("last_done"))
            if due and last and due <= last:
                report.error(where, "`due` is not after `last_done`",
                             "remove `due` after doing the routine; the next date follows from `every`")
            if not due and last and parse_every(brief.get("every")) and brief.next_due() is None:
                report.error(where, "`last_done` plus `every` is not a representable date",
                             "use a realistic interval and date")
        else:
            for key in ("every", "last_done"):
                if key in brief.meta:
                    report.error(where, f"`{key}` is only for routines",
                                 "set `kind: routine`, or use `due` for a one-off date")
        if brief.archived and status in OPEN:
            report.error(where, f"archived brief has open status `{status}`",
                         "move it back if work continues, or record why it is done or dropped")
        if not brief.archived and status in CLOSED:
            report.error(where, f"status `{status}` but not archived",
                         "if the owner accepted it, run `archive " + brief.slug + "`")
        if brief.archived:
            continue
        body = parse_front_matter(brief.text)[1]
        if status == "verifying" and not re.search(r"^##\s+Acceptance\b", body, re.M):
            report.error(where, "verifying item has no `## Acceptance` section",
                         "list the checks that remain and their results")
        if not strict:
            continue
        if not brief.get("summary"):
            report.warn(where, "missing `summary`", "add a one-line `summary:` for the index")
        if status in ("ready", "active") and not brief.routine \
                and not re.search(r"^##\s+Acceptance\b", body, re.M):
            report.warn(where, f"{status} item has no `## Acceptance` section",
                        "add observable acceptance criteria")
        if status in ("active", "verifying") and not brief.routine \
                and not re.search(r"^##\s+Progress\b", body, re.M):
            report.warn(where, f"{status} item has no `## Progress` section",
                        "add dated checkpoints and discoveries")
        if brief.routine and brief.get("last_done") and not re.search(r"^##\s+Log\b", body, re.M):
            report.warn(where, "routine has `last_done` but no `## Log` section",
                        "add a dated line for each time it was done, with what came out of it")
        targets = {os.path.normpath(os.path.join(os.path.dirname(brief.path), t.split("#", 1)[0]))
                   for t in link_targets(body)}
        if os.path.normpath(index_path) not in targets:
            report.warn(where, "no link back to the index",
                        f"add `[Roadmap]({posix_rel(index_path, os.path.dirname(brief.path))})` below the title")
        for code_path in CODE_PATH_RE.findall(re.sub(r"```.*?```", "", body, flags=re.S)):
            if not os.path.exists(os.path.normpath(os.path.join(root, code_path))):
                report.warn(where, f"code pointer `{code_path}` does not exist",
                            "refresh it, or say the file is still to be created")

    for brief in briefs:
        if not is_open(brief):
            continue
        for dep in brief.depends_on:
            target = slugs.get(dep)
            if target is None:
                report.error(rel(brief.path, root), f"depends on unknown item `{dep}`",
                             "use the slug (file name without .md) of an existing brief")
            elif target.status == "dropped":
                report.error(rel(brief.path, root), f"depends on dropped item `{dep}`",
                             "remove the dependency or replace it; dropped items never satisfy it")
            elif target.routine:
                report.error(rel(brief.path, root), f"depends on routine `{dep}`, which never finishes",
                             "depend on a separate, finishable item for whatever must happen first")
        if has_cycle(brief.slug, slugs):
            report.error(rel(brief.path, root), "dependency cycle",
                         "remove or split one of the dependencies in the cycle")


def has_cycle(start: str, slugs: Dict[str, Brief]) -> bool:
    stack = [iter(slugs[start].depends_on)]
    visited = {start}
    while stack:
        dep = next(stack[-1], None)
        if dep is None:
            stack.pop()
            continue
        if dep == start:
            return True
        if dep in slugs and dep not in visited:
            visited.add(dep)
            stack.append(iter(slugs[dep].depends_on))
    return False


# --- Commands ---------------------------------------------------------------

def paths(args: argparse.Namespace) -> Tuple[str, str, str]:
    root = os.path.abspath(args.root)
    return root, os.path.join(root, args.index), os.path.join(root, args.dir)


def disk_case(path: str, root: str,
              directory_cache: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    """`path` spelled as on disk, or None when missing. macOS accepts any
    casing, but Git, Linux and web hosts do not."""
    path, root = os.path.normpath(os.path.abspath(path)), os.path.normpath(root)
    if os.path.relpath(path, root).startswith(".."):
        return path if os.path.exists(path) else None
    current = root
    for part in os.path.relpath(path, root).split(os.sep):
        if part == ".":
            continue
        try:
            names = directory_cache.get(current) if directory_cache is not None else None
            if names is None:
                names = os.listdir(current)
                if directory_cache is not None:
                    directory_cache[current] = names
        except OSError:
            return path if os.path.exists(path) else None
        match = part if part in names else next((n for n in names if n.lower() == part.lower()), None)
        if match is None:
            return None
        current = os.path.join(current, match)
    return current


def shown(path: str, root: str) -> str:
    return rel(disk_case(path, root) or path, root)


def check_case(path: str, root: str, what: str, option: str, report: Report,
               directory_cache: Optional[Dict[str, List[str]]] = None) -> None:
    actual = disk_case(path, root, directory_cache)
    if actual and actual != os.path.normpath(os.path.abspath(path)):
        report.error(rel(actual, root), f"{what} is spelled {rel(actual, root)}, not {rel(path, root)}",
                     f"rename it with `git mv`, or pass {option} {rel(actual, root)}; "
                     "case-sensitive systems will not find it")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def foreign_layout(args: argparse.Namespace) -> bool:
    """True when a roadmap exists but has no roadmap marker and no item file
    with `status:` front matter, so per-file errors would only describe a
    migration nobody asked for."""
    root, index_path, brief_dir = paths(args)
    has_index = os.path.exists(index_path)
    if has_index:
        text = read(index_path)
        if START in text or END in text:
            return False
    found = False
    for folder in (brief_dir, os.path.join(brief_dir, "archive")):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if (not name.endswith(".md") or name.lower() == "readme.md"
                    or os.path.normpath(path) == os.path.normpath(index_path)):
                continue
            found = True
            if has_status_front_matter(read(path)):
                return False
    return has_index or found


def has_status_front_matter(text: str) -> bool:
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if re.match(r"status\s*:", line):
            return True
    return False


def cmd_init(args: argparse.Namespace) -> int:
    root, index_path, brief_dir = paths(args)
    if os.path.exists(index_path):
        print(f"{shown(index_path, root)} already exists; not overwriting it. "
              "Adapt the existing roadmap instead (see the skill's repair guidance).")
        return 1
    archive = os.path.join(brief_dir, "archive")
    os.makedirs(archive, exist_ok=True)
    if not os.listdir(archive):
        write(os.path.join(archive, ".gitkeep"), "")
    write(index_path, INDEX_TEMPLATE.format(
        dir=posix_rel(brief_dir, os.path.dirname(index_path)), start=START, end=END))
    print(f"Created {rel(index_path, root)} and {rel(archive, root)}/.")
    return cmd_sync(args)


def cmd_sync(args: argparse.Namespace) -> int:
    root, index_path, brief_dir = paths(args)
    if not os.path.exists(index_path):
        print(f"{shown(index_path, root)} not found; run `init` or pass --index.")
        return 1
    text = read(index_path)
    parts = split_index(text)
    if parts is None:
        print(f"{shown(index_path, root)} needs exactly one {START} and one {END}, in that order. "
              "Place them where the generated lists belong, then sync again.")
        return 1
    report = Report()
    briefs = load_briefs(brief_dir, report, root, index_path)
    head, _, tail = parts
    new = head + render(index_path, briefs) + tail
    if new != text:
        write(index_path, new)
        print(f"Updated {shown(index_path, root)}.")
    else:
        print(f"{shown(index_path, root)} is up to date.")
    for line in report.errors:
        print("error: " + line)
    return 1 if report.errors else 0


def cmd_check(args: argparse.Namespace) -> int:
    report, briefs = check_report(args)
    for line in report.errors:
        print("error: " + line)
    for line in report.warnings:
        print("warning: " + line)
    open_count = sum(1 for b in briefs if not b.archived)
    print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s); "
          f"{open_count} open brief(s), {len(briefs) - open_count} archived.")
    return 1 if report.errors else 0


def check_report(args: argparse.Namespace) -> Tuple[Report, List[Brief]]:
    root, index_path, brief_dir = paths(args)
    report = Report()
    directory_cache: Dict[str, List[str]] = {}
    if not os.path.isdir(brief_dir):
        report.error(rel(brief_dir, root), "brief directory not found",
                     "run `init`, or pass --dir for a roadmap kept elsewhere")
    check_case(brief_dir, root, "brief directory", "--dir", report, directory_cache)
    check_case(index_path, root, "index", "--index", report, directory_cache)
    briefs = load_briefs(brief_dir, report, root, index_path)
    if not os.path.exists(index_path):
        report.error(rel(index_path, root), "index not found",
                     "run `init`, or pass --index for a roadmap kept elsewhere")
    else:
        text = read(index_path)
        parts = split_index(text)
        if parts is None:
            report.error(rel(index_path, root), "needs exactly one pair of roadmap markers",
                         f"add {START} and {END} where the generated lists belong, then run `sync`")
        else:
            if parts[1] != render(index_path, briefs):
                report.error(rel(index_path, root), "generated lists are out of date with the briefs",
                             "run `sync`; make lasting changes in the briefs, not the generated lines")
        check_links(index_path, text, root, report, directory_cache)
    validate(briefs, index_path, root, args.strict, report)
    for brief in briefs:
        check_links(brief.path, brief.text, root, report, directory_cache)
    return report, briefs


def due_lines(briefs: List[Brief], today: datetime.date) -> List[Tuple[str, str]]:
    rows: List[Tuple[datetime.date, str, str]] = []
    for b in briefs:
        if not is_open(b) or (b.routine and horizon(b) == "later"):
            continue
        due = b.next_due()
        if b.routine and due is None and not b.get("last_done") and parse_every(b.get("every")):
            rows.append((datetime.date.min, b.slug, f"  {b.slug}: routine not done yet"))
            continue
        if due is None or (due - today).days > DUE_SOON_DAYS:
            continue
        days = (due - today).days
        when = (f"overdue since {due.isoformat()} ({-days} day(s))" if days < 0 else
                "due today" if days == 0 else f"due {due.isoformat()} (in {days} day(s))")
        rows.append((due, b.slug, f"  {b.slug}: {when}"))
    return [(slug, line) for _, slug, line in sorted(rows)]


def hold(item: Brief, status: Dict[str, str]) -> Optional[str]:
    """Why a committed item cannot be started now, or None."""
    invalid = [d for d in item.depends_on if status.get(d) in (None, "dropped")]
    if invalid:
        return f"invalid dependency {', '.join(invalid)} (see errors)"
    if item.get("blocked"):
        return f"blocked: {item.get('blocked')}"
    unmet = [d for d in item.depends_on if status.get(d) != "done"]
    if unmet:
        return f"waiting for {', '.join(unmet)}"
    return None


class Overview:
    """Due, in-progress and startable items in one roadmap."""

    def __init__(self, briefs: List[Brief], today: datetime.date):
        now, nxt, routines, _ = plan(briefs)
        self.now = now + [b for b in routines if b.status == "active"]
        self.next = nxt
        status = {b.slug: b.status for b in briefs if b.valid}
        self.held = {b.slug: hold(b, status) for b in nxt + routines}
        by_slug = {b.slug: b for b in briefs}

        def due_note(slug: str) -> Optional[str]:
            b = by_slug[slug]
            if b.status == "idea":
                return "idea, not committed"
            if b.get("when") and not b.routine:
                return f"parked until: {b.get('when')}"
            return self.held.get(slug)

        self.due = [line + (f" ({due_note(slug)})" if due_note(slug) else "")
                    for slug, line in due_lines(briefs, today)]
        self.can_start: List[str] = []
        self.needs_planning: List[str] = []
        self.waiting: List[str] = []
        for item in nxt + routines:
            if self.held[item.slug]:
                self.waiting.append(f"  {item.slug}: {self.held[item.slug]}")
            elif item.routine:
                continue
            elif item.status == "ready":
                self.can_start.append(f"  {item.slug}: {item.title}")
            else:
                self.needs_planning.append(f"  {item.slug}: {item.title}")


def cmd_ready(args: argparse.Namespace) -> int:
    root, index_path, brief_dir = paths(args)
    report = Report()
    briefs = load_briefs(brief_dir, report, root, index_path)
    validate(briefs, index_path, root, False, report)
    today = parse_date(args.today) if args.today else datetime.date.today()
    if today is None:
        print("--today must be a YYYY-MM-DD date.")
        return 1
    view = Overview(briefs, today)
    now, due, waiting = view.now, view.due, view.waiting
    if due:
        print(f"Due (overdue or within {DUE_SOON_DAYS} days):")
        print("\n".join(due))
    if now:
        print("In progress (reported, not locked):")
        for b in now:
            print(f"  {b.slug}: {b.status}" + (f", blocked: {b.get('blocked')}" if b.get("blocked") else ""))
    for label, rows in (("Ready to start:", view.can_start),
                        ("Committed, decisions still open:", view.needs_planning),
                        ("Waiting:", waiting)):
        if rows:
            print(label)
            print("\n".join(rows))
    if not (now or view.next or due or waiting):
        print("Nothing in progress, committed or due.")
    if report.errors:
        print("\nThe roadmap has errors; fix them before relying on this list:")
    for line in report.errors:
        print("error: " + line)
    return 1 if report.errors else 0


def cmd_archive(args: argparse.Namespace) -> int:
    root, index_path, brief_dir = paths(args)
    source = os.path.join(brief_dir, args.slug + ".md")
    if not os.path.exists(source):
        print(f"{rel(source, root)} not found.")
        return 1
    if not os.path.exists(index_path) or split_index(read(index_path)) is None:
        print(f"{shown(index_path, root)} is missing or lacks one pair of roadmap markers; "
              "fix that first so the index can be regenerated after the move.")
        return 1
    text = read(source)
    try:
        meta, _ = parse_front_matter(text)
    except FrontMatterError as exc:
        print(f"{rel(source, root)}: {exc}")
        return 1
    if meta.get("status") not in CLOSED:
        print(f"{rel(source, root)} has status `{meta.get('status')}`. Set `done` (after the owner "
              "accepts it) or `dropped` (with the reason in the body) before archiving.")
        return 1
    archive = os.path.join(brief_dir, "archive")
    target = os.path.join(archive, args.slug + ".md")
    if os.path.exists(target):
        print(f"{rel(target, root)} already exists; choose how to merge or rename before archiving.")
        return 1
    old = os.path.normpath(source)

    def retarget(base: str, own: bool):
        def change(link_target: str) -> str:
            if not is_local(link_target):
                return link_target
            path, sep, anchor = link_target.partition("#")
            resolved = os.path.normpath(os.path.join(os.path.dirname(source) if own else base, path))
            if resolved == old:
                new_path = posix_rel(target, base)
            elif own:
                new_path = posix_rel(resolved, archive)
            else:
                return link_target
            return new_path + sep + anchor
        return change

    updated = []
    for path in markdown_files(root):
        if os.path.normpath(path) == old:
            continue
        original = read(path)
        changed = map_links(original, retarget(os.path.dirname(path), False))
        if changed != original:
            write(path, changed)
            updated.append(rel(path, root))
    os.makedirs(archive, exist_ok=True)
    write(target, map_links(text, retarget(archive, True)))
    os.remove(source)
    gitkeep = os.path.join(archive, ".gitkeep")
    if os.path.exists(gitkeep):
        os.remove(gitkeep)
    print(f"Moved to {rel(target, root)}.")
    if updated:
        print("Updated links in: " + ", ".join(updated))
    return cmd_sync(args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["init", "sync", "check", "ready", "archive"])
    parser.add_argument("slug", nargs="?", help="brief to archive (file name without .md)")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--index", default="ROADMAP.md", help="index path relative to root")
    parser.add_argument("--dir", default="roadmap", help="brief directory relative to root")
    parser.add_argument("--strict", action="store_true", help="check: add heuristic checks")
    parser.add_argument("--today", help="ready: treat this YYYY-MM-DD date as today")
    args = parser.parse_args(argv)
    if args.command == "archive" and not args.slug:
        parser.error("archive needs the slug of the brief")
    if args.command != "init" and foreign_layout(args):
        root, index_path, brief_dir = paths(args)
        found = [shown(p, root) + suffix for p, suffix in ((index_path, ""), (brief_dir, "/"))
                 if os.path.exists(p)]
        print(f"{' and '.join(found)} {'uses' if len(found) == 1 else 'use'} a different roadmap format "
              "(no generated-list markers and no brief front matter). This script only handles "
              "its own layout: work within the existing format, and migrate only if the owner asks.")
        return 2
    commands = {"init": cmd_init, "sync": cmd_sync, "check": cmd_check,
                "ready": cmd_ready, "archive": cmd_archive}
    return commands[args.command](args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
