#!/usr/bin/env python3
"""Run the regression tests in scripts/tests in parallel batches.

Each test file is split into batches of a few tests, and each batch runs in its
own `python3 -B -m unittest` process, so tests stay as isolated as in a serial
run; most of their time is spent waiting on the scripts they start. Failing
batches print their unittest output in full. Skipped tests are counted, since a
skip is an unverified case rather than a pass. Tests run with TZ=UTC, as in CI. Exits 1 when a test fails or
errors, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "scripts" / "tests"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import output  # noqa: E402
import command_help  # noqa: E402

RUNNING = set()
LOCK = threading.Lock()


def test_ids(pattern: str) -> list:
    def flatten(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item.id()
    sys.path.insert(0, str(TESTS))
    return list(flatten(unittest.TestLoader().discover(str(TESTS), pattern=pattern, top_level_dir=str(TESTS))))


def batches(ids: list, size: int) -> list:
    modules = {}
    for test_id in ids:
        modules.setdefault(test_id.split(".", 1)[0], []).append(test_id)
    # Larger files first, so their batches are not left until the end.
    ordered = sorted(modules.values(), key=len, reverse=True)
    return [tests[start:start + size] for tests in ordered for start in range(0, len(tests), size)]


def run_batch(tests: list) -> dict:
    # Same zone as CI, so local runs catch tests that only pass in the Mac's own zone.
    env = dict(os.environ, TZ="UTC", PYTHONPATH=os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH")])))
    process = subprocess.Popen([sys.executable, "-B", "-m", "unittest", *tests], cwd=ROOT, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
    with LOCK:
        RUNNING.add(process)
    text, _ = process.communicate()
    with LOCK:
        RUNNING.discard(process)
    ran = re.search(r"^Ran (\d+) tests?", text, re.M)
    skipped = re.search(r"skipped=(\d+)", text)
    return {"tests": tests, "ok": process.returncode == 0, "output": text,
            "ran": int(ran.group(1)) if ran else 0, "skipped": int(skipped.group(1)) if skipped else 0,
            "failed": len(re.findall(r"^(?:FAIL|ERROR): ", text, re.M)) or (process.returncode != 0)}


def duration(seconds: float) -> str:
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def main(argv=None) -> int:
    parser = command_help.ArgumentParser(
        prog="run-tests.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False, epilog="""Examples:
  python3 scripts/run-tests.py
  python3 scripts/run-tests.py -p 'test_audit_repo.py'
  python3 -B -m unittest discover -s scripts/tests -p 'test_files.py'   (one file, serially)""")
    parser.add_argument("-p", "--pattern", default="test_*.py", metavar="GLOB",
                        help="test files to run (default test_*.py)")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4, metavar="N",
                        help="batches to run at once (default: the number of CPU cores)")
    parser.add_argument("--batch", type=int, default=4, metavar="N",
                        help="tests per batch (default 4)")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.batch < 1:
        parser.error("--jobs and --batch must be positive")

    start = time.monotonic()
    ids = test_ids(args.pattern)
    if not ids:
        print(f"Error: no tests match {args.pattern}", file=sys.stderr)
        return 2
    plan = batches(ids, args.batch)
    output.context(f"{len(ids)} test{'s' * (len(ids) != 1)}",
                   f"{len(plan)} batch{'es' * (len(plan) != 1)}", f"{args.jobs} at once")
    results = []
    pool = concurrent.futures.ThreadPoolExecutor(args.jobs)
    try:
        for result in pool.map(run_batch, plan):
            results.append(result)
            output.progress(f"{len(results)}/{len(plan)} batches")
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        with LOCK:
            for process in RUNNING:
                process.kill()
        raise
    finally:
        output.progress_done()
    pool.shutdown()

    failed = [result for result in results if not result["ok"]]
    for result in failed:
        output.section(", ".join(test.rsplit(".", 1)[-1] for test in result["tests"]))
        print(result["output"].rstrip())
    ran = sum(result["ran"] for result in results)
    skipped = sum(result["skipped"] for result in results)
    output.section("Summary")
    if failed:
        print(f"  {output.mark('fail')} {sum(result['failed'] for result in failed)} of {ran} tests failed")
    else:
        print(f"  {output.mark('ok')} {ran} tests passed")
    if skipped:
        print(f"  {output.mark('warn')} {skipped} skipped, not verified")
    if ran != len(ids) and not failed:
        print(f"  {output.mark('fail')} expected {len(ids)} tests, ran {ran}")
        failed = [True]
    output.note(duration(time.monotonic() - start), indent=4)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
