#!/bin/bash
# Builds a small multi-service workspace with ten nights of logs for the live investigation eval.
set -euo pipefail

workspace="${1:?usage: setup.sh <workspace>}"
mkdir -p "$workspace"

python3 - "$workspace" <<'PY'
import random
import sys
from pathlib import Path

root = Path(sys.argv[1])
rng = random.Random(42)
nights = [f"2026-11-{day:02d}" for day in range(1, 11)]
regression_from = "2026-11-06"

(root / "deploys.md").write_text("""# Deploys

| Date | Service | Version | Notes |
|---|---|---|---|
| 2026-11-05 | export-api | 1.8.0 | Structured log format for batch lines |
| 2026-11-06 | storage-gateway | 2.3.0 | Upload client refactor; config defaults moved to config/upload.yaml |
| 2026-11-07 | scheduler | 4.1.0 | Nightly export trigger moved from 01:00 to 01:30 |
""")

configs = {
    "export-api": "batch_size: 500\nupload_target: storage-gateway\n",
    "scheduler": "nightly_export:\n  cron: \"30 1 * * *\"\n",
    "storage-gateway": "upload:\n  timeout_ms: 2000\n  retries: 3\n  backoff_ms: 1500\n",
}
for service, text in configs.items():
    path = root / "services" / service / "config"
    path.mkdir(parents=True, exist_ok=True)
    name = "upload.yaml" if service == "storage-gateway" else "app.yaml"
    (path / name).write_text(text)

noise = [
    "INFO health check ok",
    "INFO gc pause 4ms",
    "DEBUG connection pool size=8 idle=6",
    "INFO metrics flushed",
    "DEBUG cache hit ratio=0.97",
]

for night in nights:
    slow = night >= regression_from
    start_hour = "01:30" if night >= "2026-11-07" else "01:00"
    export_lines, gateway_lines, scheduler_lines = [], [], []
    scheduler_lines.append(f"{night}T{start_hour}:00Z INFO triggered nightly-export")
    total = 0
    for batch in range(1, 241):
        base = rng.randint(380, 520)
        retries = 0
        if slow and rng.random() < 0.2:
            retries = rng.randint(1, 2)
            for attempt in range(1, retries + 1):
                gateway_lines.append(
                    f"{night}T{start_hour}:{batch % 60:02d}Z WARN upload batch={batch} "
                    f"timed out after 2000ms, retry {attempt}/3 in 1500ms")
        duration = base + retries * 3500
        total += duration
        if night >= "2026-11-05":
            export_lines.append(
                f'{{"ts":"{night}T{start_hour}","level":"INFO","msg":"batch uploaded",'
                f'"batch":{batch},"duration_ms":{duration}}}')
        else:
            export_lines.append(f"{night}T{start_hour} INFO batch {batch} uploaded in {duration}ms")
        gateway_lines.append(f"{night}T{start_hour}:{batch % 60:02d}Z INFO upload batch={batch} ok")
        for _ in range(4):
            export_lines.append(f"{night}T{start_hour} {rng.choice(noise)}")
            gateway_lines.append(f"{night}T{start_hour} {rng.choice(noise)}")
            scheduler_lines.append(f"{night}T{start_hour} {rng.choice(noise)}")
    export_lines.append(f"{night}T{start_hour} INFO export finished total_ms={total}")
    scheduler_lines.append(f"{night} INFO nightly-export completed")
    for service, lines in (
        ("export-api", export_lines),
        ("storage-gateway", gateway_lines),
        ("scheduler", scheduler_lines),
    ):
        folder = root / "logs" / service
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{night}.log").write_text("\n".join(lines) + "\n")
PY
