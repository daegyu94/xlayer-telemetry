"""Live check that Docker children contribute to their common cgroup v2 parent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from xlayer_telemetry.collectors.sandbox_sampler import read_cgroup


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def validate(parent_name: str, image: str, count: int) -> dict[str, object]:
    docker("image", "inspect", image)
    ids: list[str] = []
    # Keep every container alive long enough to compare the parent and children.
    workload = ('import os,time; f=open("/tmp/xlayer-io","wb"); '
                'b=b"x"*1048576; '
                '[(f.write(b),f.flush(),os.fsync(f.fileno()),time.sleep(.08)) '
                'for _ in range(48)]; f.close(); time.sleep(8)')
    try:
        for _ in range(count):
            ids.append(docker("run", "-d", "--rm", "--network", "none",
                              "--cgroup-parent", parent_name, image,
                              "python3", "-c", workload))
        paths = []
        for container_id in ids:
            pid = int(docker("inspect", "-f", "{{.State.Pid}}", container_id))
            relative = next(line.partition("::")[2] for line in
                            Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                            if "::" in line)
            paths.append(Path("/sys/fs/cgroup") / relative.lstrip("/"))
        parents = {path.parent for path in paths}
        if len(parents) != 1:
            raise RuntimeError("Docker containers do not share a cgroup parent")
        parent = parents.pop()
        before = read_cgroup(parent)
        child_before = [read_cgroup(path) for path in paths]
        before_end = read_cgroup(parent)
        time.sleep(2)
        after = read_cgroup(parent)
        child_after = [read_cgroup(path) for path in paths]
        after_end = read_cgroup(parent)
        required = {"wbytes", "cpu_usage_usec", "memory_current", "io_some_total_usec"}
        if not required <= after.keys():
            raise RuntimeError(f"missing cgroup v2 sources: {sorted(required - after.keys())}")
        if any("wbytes" not in sample for sample in (before, before_end, after_end)):
            raise RuntimeError("parent cgroup I/O source disappeared during observation")
        if not before["wbytes"] <= before_end["wbytes"] <= after["wbytes"] <= after_end["wbytes"]:
            raise RuntimeError("parent cgroup write counter reset during observation")
        parent_write = after.get("wbytes", 0) - before.get("wbytes", 0)
        parent_cpu = after["cpu_usage_usec"] - before.get("cpu_usage_usec", 0)
        child_writes = [now.get("wbytes", 0) - prior.get("wbytes", 0)
                        for now, prior in zip(child_after, child_before)]
        running = all(docker("inspect", "-f", "{{.State.Running}}", item) == "true"
                      for item in ids)
        if not running or any(value <= 0 for value in child_writes):
            raise RuntimeError("not all Docker children produced live cgroup I/O")
        # Parent and children cannot be read atomically while I/O is running.
        # Bracket each child scan with parent reads instead of requiring an
        # exact equality between counters observed at different instants.
        parent_write_min = max(0, after["wbytes"] - before_end["wbytes"])
        parent_write_max = after_end["wbytes"] - before["wbytes"]
        if not parent_write_min <= sum(child_writes) <= parent_write_max:
            raise RuntimeError("child write bytes fall outside parent observation bounds")
        if parent_cpu <= 0 or after["memory_current"] <= 0:
            raise RuntimeError("parent cgroup did not observe CPU or memory use")
        return {"containers": count, "parent_cgroup": str(parent),
                "parent_write_bytes_delta": parent_write,
                "parent_write_bytes_delta_bounds": [parent_write_min, parent_write_max],
                "comparison": "parent reads bracket non-atomic child scans",
                "children_write_bytes_delta": child_writes,
                "parent_cpu_usage_usec_delta": parent_cpu,
                "parent_memory_current_bytes": after["memory_current"]}
    finally:
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgroup-parent", default="xlayer-sandbox-validation.slice")
    parser.add_argument("--image", default="python:3.12-alpine")
    parser.add_argument("--containers", type=int, default=2)
    args = parser.parse_args()
    if args.containers < 2:
        parser.error("--containers must be at least 2")
    result = validate(args.cgroup_parent, args.image, args.containers)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
