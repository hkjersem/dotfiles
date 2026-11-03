#!/usr/bin/env python3
"""Inspect Docker storage and prune selected disposable data after confirmation."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from files import age_arg, display, output  # noqa: E402
from lib import command_help  # noqa: E402


CLEANERS = ("build-cache", "dangling-images")


def docker(context, *args):
    command = ["docker", *(["--context", context] if context else []), *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise OSError(f"{' '.join(command)} failed: "
                      f"{display(result.stderr.strip() or result.stdout.strip())}")
    return result.stdout.strip()


def json_rows(text):
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as exc:
        raise OSError("Docker returned invalid JSON") from exc


def context_identity(context):
    rows = json_rows(docker(None, "context", "inspect", context, "--format", "{{json .Endpoints}}"))
    if (len(rows) != 1 or not isinstance(rows[0], dict)
            or not isinstance(rows[0].get("docker"), dict) or not rows[0]["docker"].get("Host")):
        raise OSError("Docker returned no context endpoint")
    return rows[0]


def builder_identity(context, builder):
    rows = json_rows(docker(context, "buildx", "ls", "--format", "{{json .}}"))
    matches = [row for row in rows if isinstance(row, dict) and row.get("Name") == builder]
    if not matches:
        raise OSError(f"builder not found: {display(builder)}; select one with --builder")
    identity = None
    for row in matches:
        nodes = row.get("Nodes")
        if row.get("Err") or not row.get("Driver") or not isinstance(nodes, list) or not nodes:
            raise OSError(f"builder unavailable: {display(builder)}")
        endpoints = []
        for node in nodes:
            if not isinstance(node, dict) or not node.get("Endpoint") or node.get("Status") != "running":
                raise OSError(f"builder node is not running: {display(builder)}")
            endpoint = node["Endpoint"]
            # A node can name another context; pin and recheck its endpoint too.
            target = context_identity(endpoint) if "://" not in endpoint else endpoint
            endpoints.append((node.get("Name"), endpoint, target))
        current = (row["Driver"], endpoints)
        if identity is not None and current != identity:
            raise OSError(f"ambiguous builder: {display(builder)}")
        identity = current
    return identity


def confirm_cleanup(prompt="Clean selected Docker data?"):
    library = Path(__file__).resolve().parent / "package-manager/_update-lib.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1" || exit; confirm_apply "$2" false false',
         "clean-docker-confirm", str(library), prompt],
    )
    if result.returncode not in (0, 1):
        raise OSError(f"confirmation failed with status {result.returncode}")
    return result.returncode == 0


def parser():
    cli = argparse.ArgumentParser(
        prog="clean_docker", description=command_help.description("clean_docker"),
        epilog=command_help.help_epilog(
            "clean_docker",
            "Containers and volumes are report-only. No --all or VM disk compaction.\n"
            "The default builder is the context's own builder, not Buildx's selected builder."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cli.add_argument("--only", metavar="NAMES",
                     help="comma-separated build-cache,dangling-images; required for cleanup")
    cli.add_argument("--older-than", metavar="AGE",
                     help="retain recent data (30m, 24h, 7d, 2w); cache last use, image creation")
    cli.add_argument("--context", help="Docker context; defaults to the current context")
    cli.add_argument("--builder", help="Buildx builder for build-cache; defaults to the context name")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="show storage and inspection guidance without cleaning")
    mode.add_argument("-n", "--dry-run", action="store_true", help="show the cleanup plan without prompting or cleaning")
    return cli


def inspect_storage(context, builder, builder_target):
    output.section("Docker storage")
    storage = json_rows(docker(context, "system", "df", "--format", "{{json .}}"))
    if not storage:
        raise OSError("Docker returned an empty storage report")
    scopes = {"Images": "Dangling only", "Containers": "Report only",
              "Local Volumes": "Report only", "Build Cache": "Unused build cache"}
    for row in storage:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str)
                                                for key in ("Type", "Size", "Reclaimable")):
            raise OSError("Docker returned an invalid storage report")
        scope = scopes.get(row["Type"], "Report only")
        output.subsection(display(row["Type"]))
        output.note(f"{display(row['Size'])} total | {display(row['Reclaimable'])} reclaimable")
        output.note(output.paint(f"Cleanup scope: {scope}", "dim"))
    print()
    output.note(output.paint("Freed space may remain inside Docker's virtual disk.", "dim"))
    prefix = f"docker --context {shlex.quote(context)}"
    output.section("Inspect further")
    output.subsection("Overview")
    output.note(f"{prefix} system df --verbose")
    if builder_target is not None:
        output.subsection("Build cache")
        output.note(f"{prefix} buildx --builder {shlex.quote(builder)} du")
    if any(row["Type"] == "Containers" and row.get("TotalCount", "0") != "0" for row in storage):
        output.subsection("Containers")
        output.note(f"{prefix} container ls --all --size")
        output.note(output.paint("Inspect first; stopped containers can hold unsaved changes.", "dim"))
        output.note(output.paint("Remove one (no prompt, no --force):", "dim"))
        output.note(f"{prefix} container rm CONTAINER")
    if any(row["Type"] == "Local Volumes" and row.get("TotalCount", "0") != "0" for row in storage):
        output.subsection("Volumes")
        output.note(f"{prefix} volume ls")
        output.note(f"{prefix} volume inspect NAME")
        output.note(f"{prefix} ps --all --filter volume=NAME")
        output.note(output.paint("Back up needed data, even if no container references the volume.", "dim"))
        output.note(output.paint("Remove one (no prompt):", "dim"))
        output.note(f"{prefix} volume rm NAME")


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        seconds = age_arg(args.older_than) if args.older_than is not None else None
    except argparse.ArgumentTypeError as exc:
        cli.error(str(exc))
    if args.inspect and (args.only is not None or args.older_than is not None):
        cli.error("--inspect cannot be combined with --only or --older-than")
    names = list(dict.fromkeys(args.only.split(","))) if args.only is not None else list(CLEANERS)
    if any(name not in CLEANERS for name in names):
        cli.error("--only must contain build-cache and/or dangling-images")
    if not args.inspect and not args.dry_run and args.only is None:
        cli.error("explicit --only selection is required for cleanup; use --inspect or --dry-run to report")
    if args.builder is not None and "build-cache" not in names:
        cli.error("--builder requires build-cache selection")
    if args.dry_run:
        output.dry_run()
    try:
        if not args.context and os.environ.get("DOCKER_HOST"):
            raise OSError("DOCKER_HOST is set; select a named --context explicitly")
        context = args.context or docker(None, "context", "show")
        if not context or context.startswith("-"):
            raise OSError("invalid Docker context name")
        builder = args.builder or context
        if not builder or builder.startswith("-"):
            raise OSError("invalid Buildx builder name")
        endpoint = context_identity(context)
        builder_target = builder_identity(context, builder) if "build-cache" in names else None
        output.context(f"Docker context: {display(context)}",
                       *([f"Builder: {display(builder)}"] if builder_target else []))
        if args.inspect:
            inspect_storage(context, builder, builder_target)
            return 0
        if args.dry_run:
            output.section("Cleanup plan")
        cutoff = int(time.time()) - seconds if seconds is not None else None
        age_filter = ["--filter", f"until={seconds}s"] if seconds is not None else []
        if args.dry_run and seconds is not None:
            output.note(output.paint(f"Older than {args.older_than}; Docker rechecks eligibility.", "dim"))
        commands = []
        if "build-cache" in names:
            records = json_rows(docker(context, "buildx", "--builder", builder, "du",
                                       "--format", "json"))
            if any(not isinstance(row, dict) or not isinstance(row.get("Reclaimable"), bool)
                   or not isinstance(row.get("Shared"), bool) or not isinstance(row.get("Type"), str)
                   for row in records):
                raise OSError("Docker returned an invalid build-cache report")
            count = sum(row["Reclaimable"] and not row["Shared"]
                        and row["Type"] not in ("internal", "frontend") for row in records)
            if args.dry_run:
                estimate = (f"up to {count} unused private records before age filtering"
                            if seconds is not None else f"{count} unused private records")
                output.subsection("Build cache")
                output.note(estimate)
                output.note(output.paint("Builder endpoints: " + ", ".join(
                    display(node[1]) for node in builder_target[1]), "dim"))
                if seconds is not None:
                    output.note(output.paint("Exact last-use timestamps are unavailable; pruning applies the age filter.", "dim"))
                output.note(output.paint("Age: last use. Later builds may be slower.", "dim"))
            if count:
                commands.append(("build-cache", ["buildx", "--builder", builder, "prune",
                                                  "--force", *age_filter]))
        if "dangling-images" in names:
            ids = list(dict.fromkeys(docker(
                context, "image", "ls", "--filter", "dangling=true", "--quiet", "--no-trunc").splitlines()))
            count = len(ids)
            if ids and cutoff is not None:
                stamps = json_rows(docker(context, "image", "inspect", "--format", "{{json .Created}}",
                                           *ids))
                if len(stamps) != count:
                    raise OSError("Docker returned incomplete image timestamps")
                try:
                    count = 0
                    for stamp in stamps:
                        # Go omits trailing zeros; Python 3.9 needs 3 or 6 fractional digits.
                        normalized = re.sub(
                            r"\.(\d+)", lambda match: "." + (match[1] + "000000")[:6], stamp)
                        created = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                        count += created.timestamp() < cutoff
                except (ValueError, TypeError, AttributeError) as exc:
                    raise OSError("Docker returned invalid image timestamps") from exc
            if args.dry_run:
                estimate = f"up to {count}" if seconds is not None else str(count)
                output.subsection("Dangling images")
                output.note(f"{estimate} candidates")
                output.note(output.paint("Tagged images and images needed by containers are retained.", "dim"))
                output.note(output.paint("Age: creation, not last use.", "dim"))
                if seconds is not None:
                    output.note(output.paint("Containerd uses local image-record creation.", "dim"))
                    output.note(output.paint("Recently pulled old images may be retained.", "dim"))
            if count:
                commands.append(("dangling-images", ["image", "prune", "--force",
                                                     *(["--filter", f"until={cutoff}"] if cutoff is not None else [])]))
        if args.dry_run:
            return 0
        age = f" older than {args.older_than}" if seconds is not None else ""
        if commands and not confirm_cleanup(f"Clean {', '.join(names)}{age}?"):
            output.row("info", "Docker cleanup", "Cancelled; nothing cleaned.")
            return 0
        output.section("Cleanup results")
        for name in names:
            if not any(selected == name for selected, _ in commands):
                output.row("info", name, "Nothing eligible to clean.")
        for name, command in commands:
            if context_identity(context) != endpoint:
                raise OSError("Docker context endpoint changed after checks; nothing further cleaned")
            if builder_target is not None and builder_identity(context, builder) != builder_target:
                raise OSError("Buildx builder changed after checks; nothing further cleaned")
            detail = docker(context, *command)
            output.row("ok", name, "Cleaned")
            for line in detail.splitlines():
                output.note(display(line))
        return 0
    except OSError as exc:
        output.row("fail", "Docker cleanup", display(exc))
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        sys.exit(130)
