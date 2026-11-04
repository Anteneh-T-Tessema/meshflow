"""Validate an isolated offline Docker deployment; never connect to production."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Existing local runtime image")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    name = f"meshflow-staging-{uuid.uuid4().hex[:12]}"
    volume = name + "-data"
    checks: list[str] = []
    receipt = {"image": args.image, "checks": checks, "provider": "offline mock"}
    container_created = False
    volume_created = False
    try:
        image_id = docker("image", "inspect", "--format", "{{.Id}}", args.image)
        receipt["image_id"] = image_id
        docker("volume", "create", volume)
        volume_created = True
        docker(
            "run", "--detach", "--name", name, "--publish", "127.0.0.1::8000",
            "--env", "MESHFLOW_MOCK=1", "--env", "MESHFLOW_LEDGER_DB=/data/runs.db",
            "--env", f"MESHFLOW_WEBHOOK_SECRET={uuid.uuid4().hex}",
            "--volume", f"{volume}:/data", args.image,
        )
        container_created = True
        port = docker("port", name, "8000/tcp").rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"

        def request(path, key=None, body=None):
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = Request(
                base + path, headers=headers,
                data=json.dumps(body).encode() if body is not None else None,
            )
            try:
                with urlopen(req, timeout=20) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                return error.code, json.loads(error.read())

        def ready():
            for _ in range(80):
                try:
                    if request("/health/live")[0] == 200:
                        return
                except (URLError, ConnectionError, TimeoutError):
                    pass
                time.sleep(0.25)
            raise RuntimeError("Staging did not become live")

        def check(condition, label):
            if not condition:
                raise RuntimeError(f"Staging check failed: {label}")
            checks.append(label)

        ready()
        check(request("/health/live")[0] == 200, "liveness")
        check(request("/health/ready")[0] == 200, "readiness")
        # Credentials are created in staging and kept in process memory only.
        setup = '''
import json
from meshflow.security.api_keys import KeyStore
s=KeyStore('/data/runs.db')
print(json.dumps({
 'operator': s.create('operator', role='operator', tenant_id='tenant-a'),
 'viewer': s.create('viewer', role='viewer', tenant_id='tenant-a'),
 'other': s.create('other', role='viewer', tenant_id='tenant-b'),
}))
'''
        keys = json.loads(docker("exec", name, "python", "-c", setup))
        check(request("/traces")[0] == 401, "anonymous trace denial")
        check(request("/traces", "invalid")[0] == 401, "invalid-key denial")
        operator_id, operator_key = keys["operator"]
        viewer_key, other_key = keys["viewer"][1], keys["other"][1]
        task = {"task": "Summarize quarterly revenue", "policy": {
            "mode": "dev", "enable_uncertainty": False, "deterministic_gate": False,
        }, "context": {"_tenant_id": "tenant-b"}}
        for route in ("/run", "/stream"):
            check(request(route, viewer_key, task)[0] == 403, f"viewer denial {route}")
        status, result = request("/run", operator_key, task)
        receipt["completion_http_status"] = status
        if status != 200:
            # This isolated deployment has no provider credentials or user data.
            receipt["completion_error"] = result
        check(status == 200, "operator completion")
        run_id = result["run_id"]
        receipt["run_id"] = run_id
        trace_route = f"/traces/{run_id}"
        status, trace = request(trace_route, operator_key)
        check(status == 200 and bool(trace["steps"]), "completion-to-trace round trip")
        check(all(step["tenant_id"] == "tenant-a" for step in trace["steps"]),
              "authenticated tenant ownership")
        check(request(trace_route, other_key)[0] == 404, "cross-tenant trace denial")
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: request(trace_route, viewer_key)[0], range(32)))
        check(all(status == 200 for status in statuses), "32 concurrent authorized reads")
        docker("restart", name)
        # Docker can assign a new ephemeral host port when restarting.
        port = docker("port", name, "8000/tcp").rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        ready()
        check(request(trace_route, operator_key)[0] == 200, "audit survives server restart")
        revoke = (
            "from meshflow.security.api_keys import KeyStore; "
            f"KeyStore('/data/runs.db').revoke({operator_id!r})"
        )
        docker("exec", name, "python", "-c", revoke)
        check(request(trace_route, operator_key)[0] == 401, "revoked-key denial")
        receipt["status"] = "passed"
    except BaseException:
        receipt["status"] = "failed"
        raise
    finally:
        if container_created:
            if receipt.get("status") == "failed":
                logs = subprocess.run(
                    ["docker", "logs", name], capture_output=True, text=True, check=False
                )
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.with_suffix(".container.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", name)
        if volume_created:
            docker("volume", "rm", volume)
        receipt["cleanup"] = "completed"
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Passed {len(checks)} Docker staging checks; receipt: {args.receipt}")


if __name__ == "__main__":
    main()
