#!/usr/bin/env python3
"""Run repo-authored skill evals in isolated Copilot CLI sessions.

Each case runs with the skill available and with skills excluded, in a fresh
workspace outside this repository, so its instructions and docs do not help
either run. A case's `setup` script builds a disposable repository in that
workspace first; otherwise its `files` are copied there. Results land in the
skill-creator layout (<out>/<skill>/eval-<id>/<arm>/run-<n>/) with the answer,
transcript, usage and timing. With --grade, a fresh Copilot CLI session grades
a copy of each run, in a folder whose name does not reveal the arm, against the
case's assertions and writes grading.json beside the run. The summary compares
the arms, lists assertions every run passed outside guard cases (which pin
behaviour the agent already shows without the skill), and saves summary.json
with the runner and grader models. --regrade DIR grades an existing results
folder. Without SKILL arguments it lists the cases and runs nothing. Exits 1
when a run or grading fails, the skill was not used where available or used
where excluded, or this repository changed during the runs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "agents" / "skills"
ARMS = ("with_skill", "without_skill")

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import output  # noqa: E402
import command_help  # noqa: E402


def home_short(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def catalogs() -> dict:
    found = {}
    for evals in sorted(SKILLS.glob("*/evals/evals.json")):
        found[evals.parents[1].name] = json.loads(evals.read_text(encoding="utf-8"))["evals"]
    return found


def select(specs: List[str], available: dict) -> list:
    chosen = []
    for spec in specs:
        skill, _, ident = spec.partition(":")
        if skill not in available:
            raise ValueError(f"unknown skill {skill!r}; skills with evals: {', '.join(available)}")
        cases = available[skill]
        if ident:
            if not ident.isdigit() or not any(case["id"] == int(ident) for case in cases):
                raise ValueError(f"{skill} has no eval {ident!r}")
            cases = [case for case in cases if case["id"] == int(ident)]
        chosen.extend((skill, case) for case in cases if (skill, case) not in chosen)
    return chosen


def prepare(skill: str, case: dict, workspace: Path) -> Path:
    skill_dir = SKILLS / skill
    workspace.mkdir(parents=True)
    if case.get("setup"):
        subprocess.run(["bash", str(skill_dir / case["setup"]), str(workspace)], check=True,
                       stdout=subprocess.DEVNULL)
    elif case.get("files"):
        paths = [Path(path) for path in case["files"]]
        base = Path(os.path.commonpath([str(path.parent) for path in paths]))
        for path in paths:
            target = workspace / path.relative_to(base)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_dir / path, target)
    return workspace / case.get("workdir", "")


def run_one(skill: str, case: dict, arm: str, number: int, out: Path, model: str,
            timeout: int) -> dict:
    run = out / skill / f"eval-{case['id']}" / arm / f"run-{number}"
    outputs = run / "outputs"
    outputs.mkdir(parents=True)
    result = {"skill": skill, "id": case["id"], "arm": arm, "run": number, "ok": False}
    try:
        workdir = prepare(skill, case, run / "workspace")
    except (OSError, subprocess.CalledProcessError) as error:
        result["error"] = f"setup failed: {error}"
        return result
    command = ["copilot", "-p", case["prompt"], "--allow-all", "-s", "--no-auto-update",
               "--share", str(outputs / "transcript.md"),
               "--usage-output-file", str(outputs / "usage.json")]
    if arm == "without_skill":
        command += ["--excluded-tools", "skill"]
    if model:
        command += ["--model", model]
    start = time.monotonic()
    try:
        with open(outputs / "answer.md", "w") as answer, open(outputs / "stderr.txt", "w") as err:
            status = subprocess.run(command, cwd=workdir, stdout=answer, stderr=err,
                                    timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        status = None
        result["error"] = f"timed out after {timeout}s"
    seconds = round(time.monotonic() - start, 1)
    timing = {"total_duration_seconds": seconds}
    try:
        usage = json.loads((outputs / "usage.json").read_text())
        timing["total_tokens"] = sum(v["tokenCount"] for v in usage["tokenDetails"].values())
        result["models"] = sorted(usage.get("modelMetrics", {}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    (run / "timing.json").write_text(json.dumps(timing) + "\n")
    try:
        transcript = (outputs / "transcript.md").read_text()
    except OSError:
        transcript = ""
    result["used_skill"] = "\n### `skill`" in transcript
    result["seconds"] = seconds
    if status not in (0, None):
        result["error"] = f"copilot exited {status}"
    result["ok"] = status == 0
    return result


GRADER_PROMPT = """You are grading one run of an AI agent against fixed assertions.
This folder holds the run: outputs/answer.md is its final answer, outputs/transcript.md
its full session including every command it ran, and workspace/ the files it worked in,
as it left them. assertions.json lists the assertions and prompt.txt the task it got.

Grade every assertion, in order, from evidence in these files. The burden of proof is
on the assertion: pass it only when the transcript or files clearly show it is true and
reflect real work, not surface compliance. A claim that something was run or verified
passes only if the transcript shows the command and its output. An assertion that the
repository or files stayed unchanged fails if the transcript shows any command that
wrote to them, even one later undone (stash, checkout, reset, commit, merge, rebase), or
if generated files such as __pycache__ were left behind. Inspect the workspace read-only.

Write grading.json next to assertions.json, not inside outputs/, with exactly this shape, one entry per assertion, with
"text" copied verbatim from assertions.json:
{"expectations": [{"text": "...", "passed": true, "evidence": "quote or concrete finding"}],
 "eval_feedback": "optional: an assertion that would pass for a wrong answer, or an
 important outcome no assertion checks; empty when none"}
Then reply with one line: the number of passed assertions out of the total."""


def run_dirs(out: Path) -> list:
    return sorted(out.glob("*/eval-*/*/run-*"))


def grade_one(run: Path, assertions: List[str], prompt: str, model: str, timeout: int) -> dict:
    parts = run.relative_to(run.parents[3]).parts
    result = {"skill": parts[0], "id": int(parts[1].split("-", 1)[1]), "arm": parts[2],
              "run": int(parts[3].split("-", 1)[1]), "ok": False}
    with tempfile.TemporaryDirectory(prefix="grade-") as temporary:
        folder = Path(temporary) / "run"
        shutil.copytree(run / "outputs", folder / "outputs", symlinks=True)
        if (run / "workspace").is_dir():
            shutil.copytree(run / "workspace", folder / "workspace", symlinks=True)
        (folder / "assertions.json").write_text(json.dumps(assertions, indent=2) + "\n")
        (folder / "prompt.txt").write_text(prompt + "\n")
        command = ["copilot", "-p", GRADER_PROMPT, "--allow-all", "-s", "--no-auto-update",
                   "--excluded-tools", "skill", "--usage-output-file", str(folder.parent / "usage.json")]
        if model:
            command += ["--model", model]
        try:
            status = subprocess.run(command, cwd=folder, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            result["error"] = f"grading timed out after {timeout}s"
            return result
        if status != 0:
            result["error"] = f"grader exited {status}"
            return result
        try:
            grading = json.loads((folder / "grading.json").read_text())
            graded = grading["expectations"]
            # Scores are matched to assertions by position, so the grader must echo each one.
            if [str(entry.get("text", "")).strip() for entry in graded] != [
                    text.strip() for text in assertions] or not all(
                    isinstance(entry.get("passed"), bool) for entry in graded):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            result["error"] = "no valid grading.json covering every assertion in order"
            return result
        result["grader_models"] = models(folder.parent / "usage.json")
    expectations = [{"text": text, "passed": entry["passed"],
                     "evidence": str(entry.get("evidence", ""))}
                    for text, entry in zip(assertions, graded)]
    passed = sum(entry["passed"] for entry in expectations)
    (run / "grading.json").write_text(json.dumps({
        "expectations": expectations,
        "summary": {"passed": passed, "failed": len(expectations) - passed,
                    "total": len(expectations),
                    "pass_rate": round(passed / len(expectations), 2) if expectations else 0},
        "eval_feedback": str(grading.get("eval_feedback", "")),
    }, indent=2) + "\n")
    result.update(ok=True, passed=[entry["passed"] for entry in expectations])
    return result


def models(usage: Path) -> List[str]:
    try:
        return sorted(json.loads(usage.read_text()).get("modelMetrics", {}))
    except (OSError, ValueError, AttributeError):
        return []


def grade(out: Path, model: str, jobs: int, timeout: int) -> int:
    runs = run_dirs(out)
    if not runs:
        print(f"Error: no runs in {home_short(out)}.", file=sys.stderr)
        return 1
    tasks = []
    guards = set()
    runners = set()
    for run in runs:
        metadata = json.loads((run.parents[1] / "eval_metadata.json").read_text())
        tasks.append((run, metadata["assertions"], metadata["prompt"]))
        if metadata.get("guard"):
            guards.add((run.parents[2].name, metadata["eval_id"]))
        runners.update(models(run / "outputs" / "usage.json"))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(grade_one, *task, model, timeout) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            output.progress(f"{len(results)}/{len(tasks)} runs graded")
    output.progress_done()

    problems = 0
    cases = {}
    graders = set()
    for result in results:
        if not result["ok"]:
            problems += 1
            continue
        graders.update(result.get("grader_models", []))
        cases.setdefault((result["skill"], result["id"]), {}).setdefault(
            result["arm"], []).append(result["passed"])
    output.section("Grades")
    if cases:
        output.table_header("%-20s %12s %14s %6s", "Case", "with_skill", "without_skill", "Gap")
    summary = {"runner_models": sorted(runners), "grader_models": sorted(graders), "cases": {}}
    for (skill, ident), arms in sorted(cases.items()):
        cells, rates = [], {}
        for arm in ARMS:
            grades = arms.get(arm, [])
            passed = sum(sum(run) for run in grades)
            total = sum(len(run) for run in grades)
            cells.append(f"{passed}/{total} {round(100 * passed / total)}%" if total else "-")
            if total:
                rates[arm] = passed / total
        gap = round(100 * (rates["with_skill"] - rates["without_skill"])) if len(rates) == 2 else None
        name = f"{skill}:{ident}" + (" guard" if (skill, ident) in guards else "")
        print("  %-20s %12s %14s %6s" % (name, cells[0], cells[1], "-" if gap is None else f"{gap:+d}"))
        summary["cases"][f"{skill}:{ident}"] = {
            "guard": (skill, ident) in guards, "gap": gap,
            **{arm: [sum(run) for run in arms.get(arm, [])] for arm in ARMS}}
    for result in sorted(results, key=lambda r: (r["skill"], r["id"], r["arm"], r["run"])):
        if not result["ok"]:
            output.row("fail", f"{result['skill']}:{result['id']} {result['arm']} {result['run']}",
                       result["error"])

    always = []
    for (skill, ident), arms in sorted(cases.items()):
        grades = [run for arm in ARMS for run in arms.get(arm, [])]
        if all(arm in arms for arm in ARMS) and grades and (skill, ident) not in guards:
            always += [(skill, ident, index + 1) for index in range(len(grades[0]))
                       if all(run[index] for run in grades)]
    if always:
        output.section("Passed in every run")
        for skill, ident, number in always:
            output.row("info", f"{skill}:{ident}", f"assertion {number} does not separate the arms")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    output.note(f"Runner {', '.join(summary['runner_models']) or 'unknown'}; grader "
                f"{', '.join(summary['grader_models']) or 'unknown'}", indent=2)
    output.note(f"Evidence: grading.json in each run and summary.json under {home_short(out)}", indent=2)
    return problems


def repo_state() -> str:
    status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                            capture_output=True, text=True)
    diff = subprocess.run(["git", "-C", str(REPO), "diff"], capture_output=True, text=True)
    return status.stdout + diff.stdout


def list_cases(available: dict) -> None:
    output.section("Eval cases")
    for skill, cases in available.items():
        output.subsection(skill)
        for case in cases:
            extra = " (builds a repository)" if case.get("setup") else ""
            print(f"    {skill}:{case['id']}  {case['prompt'][:70].strip()}…{extra}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = command_help.ArgumentParser(
        prog="skill-evals.py",
        description=__doc__.split("\n\n", 1)[0] + "\n\nStarts Copilot CLI sessions that use AI credits; runs work in disposable"
        " folders.\nA development tool for this repository, run as python3 scripts/skill-evals.py.\n\n"
        + __doc__.split("\n\n", 1)[1],
        allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Grading: --grade grades after the runs, --regrade DIR grades earlier results; graders
use AI credits too. See docs/agent-skills.md.
Exit status: 0 success; 1 failed or suspect runs or grading; 2 usage.

Examples:
  python3 scripts/skill-evals.py test-plan --dry-run
  python3 scripts/skill-evals.py blast-radius:2 --runs 3 --grade
  python3 scripts/skill-evals.py test-plan --model gpt-6.1-sol --grader-model claude-sonnet-5.5
  python3 scripts/skill-evals.py --regrade ~/.cache/dotfiles/skill-evals/<timestamp>""")
    parser.add_argument("specs", nargs="*", metavar="SKILL[:ID]",
                        help="skill, or one of its cases, to run; omit to list cases")
    parser.add_argument("--runs", type=int, default=1, metavar="N",
                        help="runs per case and arm (default 1)")
    parser.add_argument("--model", default="", metavar="MODEL",
                        help="Copilot CLI model for both arms (default: the CLI default)")
    parser.add_argument("--jobs", type=int, default=4, metavar="N",
                        help="parallel agent sessions (default 4)")
    parser.add_argument("--timeout", type=int, default=900, metavar="SECONDS",
                        help="stop a run after this many seconds (default 900)")
    parser.add_argument("--out", metavar="DIR",
                        help="results folder (default ~/.cache/dotfiles/skill-evals/<timestamp>)")
    parser.add_argument("--grade", action="store_true",
                        help="grade each run with a fresh agent after the runs")
    parser.add_argument("--regrade", metavar="DIR",
                        help="grade an existing results folder instead of running cases")
    parser.add_argument("--grader-model", default="", metavar="MODEL",
                        help="Copilot CLI model for grading (default: the CLI default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the planned runs without starting agents")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.jobs < 1 or args.timeout < 1:
        parser.error("--runs, --jobs and --timeout must be positive")

    if args.regrade:
        if args.specs or args.grade or args.dry_run:
            parser.error("--regrade takes no SKILL arguments, --grade or --dry-run")
        if not shutil.which("copilot"):
            print("Error: copilot (GitHub Copilot CLI) is not installed.", file=sys.stderr)
            return 1
        regrade = Path(args.regrade).expanduser().resolve()
        output.context(home_short(regrade), args.grader_model or "default grader model")
        return 1 if grade(regrade, args.grader_model, args.jobs, args.timeout) else 0

    available = catalogs()
    if not args.specs:
        list_cases(available)
        return 0
    try:
        chosen = select(args.specs, available)
    except ValueError as error:
        parser.error(str(error))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # Absolute, because each run starts in its own workspace.
    out = Path(args.out).expanduser().resolve() if args.out else (
        Path.home() / ".cache" / "dotfiles" / "skill-evals" / stamp)
    plan = [(skill, case, arm, number) for skill, case in chosen for arm in ARMS
            for number in range(1, args.runs + 1)]

    if args.dry_run:
        output.dry_run()
    output.context(home_short(out), f"{len(plan)} runs" + (", graded" if args.grade else ""),
                   args.model or "default model")
    if args.dry_run:
        output.section("Planned runs")
        for skill, case, arm, number in plan:
            output.row("info", f"{skill}:{case['id']}", f"{arm} run {number}")
        return 0
    if not shutil.which("copilot"):
        print("Error: copilot (GitHub Copilot CLI) is not installed.", file=sys.stderr)
        return 1
    if out.exists() and any(out.iterdir()):
        print(f"Error: {home_short(out)} is not empty.", file=sys.stderr)
        return 1

    for skill, case in chosen:
        folder = out / skill / f"eval-{case['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "eval_metadata.json").write_text(json.dumps({
            "eval_id": case["id"], "eval_name": f"eval-{case['id']}", "prompt": case["prompt"],
            "assertions": case["assertions"], "guard": case.get("guard", False)}, indent=2) + "\n")
    before = repo_state()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, *entry, out, args.model, args.timeout) for entry in plan]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            output.progress(f"{len(results)}/{len(plan)} runs finished")
    output.progress_done()
    changed = repo_state() != before

    problems = 0
    output.section("Runs")
    for result in sorted(results, key=lambda r: (r["skill"], r["id"], r["arm"], r["run"])):
        label = f"{result['skill']}:{result['id']} {result['arm']} {result['run']}"
        expected = result["arm"] == "with_skill"
        detail = f"{result.get('seconds', 0)}s"
        if result.get("models"):
            detail += f", {', '.join(result['models'])}"
        if not result["ok"]:
            problems += 1
            output.row("fail", label, result.get("error", "failed"))
        elif result["used_skill"] != expected:
            problems += 1
            output.row("warn", label, detail + (", skill not used" if expected else ", skill used"))
        else:
            output.row("ok", label, detail)
    if changed:
        problems += 1
        output.section("Repository")
        output.row("warn", "~/.dotfiles", "changed during the runs; check whether a run edited it")
    if args.grade:
        problems += grade(out, args.grader_model, args.jobs, args.timeout)
        return 1 if problems else 0
    output.section("Next")
    output.note(f"Results: {home_short(out)}", indent=2)
    output.note(f"Grade them with: skill-evals --regrade {home_short(out)}", indent=2)
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
