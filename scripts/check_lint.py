"""Run the blocking repository lint check used by CI."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "ruff", "check", "meshflow/", "--select", "E,W,F",
     "--ignore", "E501,F401,F811"],
    cwd=Path(__file__).resolve().parents[1],
)
sys.exit(result.returncode)
