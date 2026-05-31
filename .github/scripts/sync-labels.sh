#!/usr/bin/env bash
# Sync GitHub labels from .github/labels.yml
# Usage: .github/scripts/sync-labels.sh [owner/repo]
#
# Requires: gh CLI authenticated, python3, PyYAML
#   pip install pyyaml
#   gh auth login

set -euo pipefail

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
LABELS_FILE="$(git rev-parse --show-toplevel)/.github/labels.yml"

echo "Syncing labels to $REPO from $LABELS_FILE..."

python3 - <<'PYEOF'
import subprocess, sys, yaml

with open("$(git rev-parse --show-toplevel)/.github/labels.yml") as f:
    labels = yaml.safe_load(f)

repo = sys.argv[1] if len(sys.argv) > 1 else None
for label in labels:
    name  = label["name"]
    color = label["color"].lstrip("#")
    desc  = label.get("description", "")
    cmd = ["gh", "label", "create", name,
           "--color", color, "--description", desc,
           "--force"]
    if repo:
        cmd += ["-R", repo]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}: {result.stderr.strip()}")
PYEOF
