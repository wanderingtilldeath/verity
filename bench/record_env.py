"""Write bench/results/env.json: machine, ES version/settings and index stats for a benchmark run."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

from elasticsearch import Elasticsearch

from app.config import get_settings


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    s = get_settings()
    es = Elasticsearch(s.es_url)
    info = es.info()
    stats = es.indices.stats(index=s.es_alias, metric="store,docs,segments")["_all"]["primaries"]
    out = {
        "label": label,
        "platform": platform.platform(),
        "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "cores": sh(["sysctl", "-n", "hw.ncpu"]),
        "ram_bytes": sh(["sysctl", "-n", "hw.memsize"]),
        "docker": sh(["docker", "info", "--format", "mem={{.MemTotal}} cpus={{.NCPU}}"]),
        "k6": sh(["k6", "version"]),
        "es_version": info["version"]["number"],
        "index": {
            "docs": stats["docs"]["count"],
            "size_bytes": stats["store"]["size_in_bytes"],
            "segments": stats["segments"]["count"],
        },
        "note": "k6 and Elasticsearch share this machine: latency is laptop/single-node, not production",
    }
    path = Path("bench/results") / f"env_{label}.json"
    path.write_text(json.dumps(out, indent=2))
    print(path)


if __name__ == "__main__":
    main()
