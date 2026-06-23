#!/usr/bin/env python3
"""Memory leak test for long-running workflows.

Runs a workflow repeatedly in sandbox mode and tracks RSS memory growth.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys

from meshflow import Workflow, Agent


def get_current_rss_kb() -> float:
    """Return current RSS memory usage of this process in kilobytes."""
    try:
        pid = os.getpid()
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)])
        return float(out.decode().strip())
    except Exception:
        # Fallback to peak RSS if ps fails
        import resource
        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return maxrss / 1024.0
        return float(maxrss)


def main() -> None:
    print()
    print("  \033[1m\033[36mMeshFlow Memory Leak Profiler\033[0m")
    print("  " + "─" * 50)
    print("  Running workflow iterations in sandbox mode...")

    # Warm-up runs to let imports, module caches, and sqlite schemas compile/stabilize
    warmup_iters = 50
    for i in range(warmup_iters):
        wf = Workflow(mode="sandbox")
        wf.add(Agent("planner"), Agent("executor"))
        wf.run("Task description")

    gc.collect()
    mem_start = get_current_rss_kb()
    print(f"  Warm-up completed ({warmup_iters} runs). Initial RSS: \033[33m{mem_start / 1024:.2f} MB\033[0m")
    print("  " + "─" * 50)
    print(f"  {'Iteration':>10} | {'Current RSS (MB)':>18} | {'Delta (MB)':>12}")
    print("  " + "─" * 50)

    test_iters = 200
    for i in range(1, test_iters + 1):
        wf = Workflow(mode="sandbox")
        wf.add(Agent("planner"), Agent("executor"))
        wf.run(f"Iterative task execution {i}")

        if i % 50 == 0:
            gc.collect()
            current_mem = get_current_rss_kb()
            delta = (current_mem - mem_start) / 1024.0
            print(f"  {i:>10d} | {current_mem / 1024.0:>18.2f} | {delta:>+11.2f}")

    gc.collect()
    mem_end = get_current_rss_kb()
    total_leak_mb = (mem_end - mem_start) / 1024.0

    print("  " + "─" * 50)
    print(f"  Final Memory RSS   : {mem_end / 1024.0:.2f} MB")
    print(f"  Net Memory Delta   : \033[1m{total_leak_mb:+.2f} MB\033[0m")

    # Threshold for test failure: 5.0 MB
    threshold_mb = 5.0
    if total_leak_mb > threshold_mb:
        print(f"  \033[31m[FAIL] Memory leak detected! Growth exceeds threshold of {threshold_mb} MB.\033[0m")
        print()
        sys.exit(1)
    else:
        print(f"  \033[32m[PASS] Memory usage stable (within threshold of {threshold_mb} MB).\033[0m")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
