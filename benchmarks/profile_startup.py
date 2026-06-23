#!/usr/bin/env python3
"""Startup time benchmark for MeshFlow.

Measures import speed and imports count in a fresh python process.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    print()
    print("  \033[1m\033[36mMeshFlow Startup Time Benchmark\033[0m")
    print("  " + "─" * 40)

    # 1. Measure import time over 5 runs
    times: list[float] = []
    for i in range(5):
        cmd = [
            sys.executable,
            "-c",
            "import time; t0 = time.perf_counter(); import meshflow; print(time.perf_counter() - t0)",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        times.append(float(result.stdout.strip()))

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    # 2. Count imported modules
    cmd_modules = [
        sys.executable,
        "-c",
        "import sys; import meshflow; print(len(sys.modules))",
    ]
    result_modules = subprocess.run(cmd_modules, capture_output=True, text=True, check=True)
    modules_count = int(result_modules.stdout.strip())

    # 3. Check clean python standard modules count for baseline
    cmd_baseline = [
        sys.executable,
        "-c",
        "import sys; print(len(sys.modules))",
    ]
    result_baseline = subprocess.run(cmd_baseline, capture_output=True, text=True, check=True)
    baseline_count = int(result_baseline.stdout.strip())

    added_modules = modules_count - baseline_count

    print(f"  Imports Count   : \033[32m{modules_count}\033[0m (baseline: {baseline_count}, added: {added_modules})")
    print(f"  Startup Time    :")
    print(f"    Average       : \033[1m\033[36m{avg_time * 1000:.1f} ms\033[0m")
    print(f"    Min           : {min_time * 1000:.1f} ms")
    print(f"    Max           : {max_time * 1000:.1f} ms")
    print("  " + "─" * 40)
    print()


if __name__ == "__main__":
    main()
