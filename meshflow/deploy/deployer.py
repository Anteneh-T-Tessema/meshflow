"""meshflow deploy — build and run MeshFlow via Docker or docker-compose.

Usage::

    from meshflow.deploy.deployer import DockerDeployer

    dep = DockerDeployer(tag="meshflow:latest")
    result = dep.build()
    print(result.image_id)

    result = dep.run(port=8000, env_file=".env")
    print(result.container_id)

CLI::

    meshflow deploy                   # build + run with docker-compose
    meshflow deploy --build-only      # just build the image
    meshflow deploy --tag my-tag      # custom image tag
    meshflow deploy --compose-profile postgres  # with postgres
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class DeployResult:
    """Result of a deploy operation."""

    ok: bool
    command: str
    stdout: str
    stderr: str
    duration_ms: float
    image_id: str = ""
    container_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "image_id": self.image_id,
            "container_id": self.container_id,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
        }


class DockerDeployer:
    """Build and run MeshFlow via Docker.

    Parameters
    ----------
    tag:         Docker image tag (default: ``meshflow:latest``).
    dockerfile:  Path to the Dockerfile (default: auto-detected from CWD).
    context:     Docker build context directory (default: CWD).
    timeout:     Subprocess timeout in seconds (default: 300 = 5 min).
    """

    def __init__(
        self,
        tag: str = "meshflow:latest",
        dockerfile: str = "",
        context: str = ".",
        timeout: int = 300,
    ) -> None:
        self._tag = tag
        self._dockerfile = dockerfile or self._find_dockerfile(context)
        self._context = context
        self._timeout = timeout

    # ── Build ──────────────────────────────────────────────────────────────────

    def build(self, *, no_cache: bool = False) -> DeployResult:
        """Build the Docker image.

        Parameters
        ----------
        no_cache: Pass ``--no-cache`` to force a fresh build.
        """
        if not shutil.which("docker"):
            return DeployResult(
                ok=False, command="docker build", stdout="", stderr="",
                duration_ms=0, error="docker not found in PATH",
            )

        cmd = ["docker", "build", "-t", self._tag]
        if self._dockerfile:
            cmd += ["-f", self._dockerfile]
        if no_cache:
            cmd += ["--no-cache"]
        cmd.append(self._context)

        return self._run_cmd(cmd)

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(
        self,
        port: int = 8000,
        *,
        env_file: str = "",
        detach: bool = True,
        name: str = "meshflow",
        data_volume: str = "meshflow_data",
        extra_env: dict[str, str] | None = None,
    ) -> DeployResult:
        """Run the image as a container.

        Parameters
        ----------
        port:         Host port to map to container port 8000.
        env_file:     Path to a .env file (passed via --env-file).
        detach:       Run in background (default True).
        name:         Container name.
        data_volume:  Named volume for /data persistence.
        extra_env:    Extra -e KEY=VALUE pairs.
        """
        if not shutil.which("docker"):
            return DeployResult(
                ok=False, command="docker run", stdout="", stderr="",
                duration_ms=0, error="docker not found in PATH",
            )

        cmd = ["docker", "run"]
        if detach:
            cmd.append("-d")
        cmd += ["--name", name, "-p", f"{port}:8000",
                "-v", f"{data_volume}:/data", "--restart", "unless-stopped"]
        if env_file and os.path.exists(env_file):
            cmd += ["--env-file", env_file]
        for k, v in (extra_env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd.append(self._tag)

        result = self._run_cmd(cmd)
        if result.ok:
            result.container_id = result.stdout.strip()[:12]
        return result

    def stop(self, name: str = "meshflow") -> DeployResult:
        """Stop and remove the named container."""
        cmd = ["docker", "rm", "-f", name]
        return self._run_cmd(cmd)

    def logs(self, name: str = "meshflow", *, tail: int = 50) -> str:
        """Fetch container logs."""
        if not shutil.which("docker"):
            return "docker not found"
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), name],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout + result.stderr

    # ── docker-compose ─────────────────────────────────────────────────────────

    def compose_up(
        self,
        compose_file: str = "docker-compose.yml",
        *,
        profiles: list[str] | None = None,
        build: bool = True,
        detach: bool = True,
    ) -> DeployResult:
        """Run ``docker compose up``.

        Parameters
        ----------
        profiles:   Docker Compose profiles to activate (e.g. ``["postgres"]``).
        build:      Pass ``--build`` to rebuild the image.
        detach:     Run in background.
        """
        compose_cmd = self._compose_executable()
        if not compose_cmd:
            return DeployResult(
                ok=False, command="docker compose up", stdout="", stderr="",
                duration_ms=0, error="Neither 'docker compose' nor 'docker-compose' found.",
            )

        cmd = compose_cmd + ["-f", compose_file, "up"]
        if build:
            cmd.append("--build")
        if detach:
            cmd.append("-d")
        for profile in (profiles or []):
            cmd += ["--profile", profile]

        return self._run_cmd(cmd)

    def compose_down(self, compose_file: str = "docker-compose.yml") -> DeployResult:
        """Run ``docker compose down``."""
        compose_cmd = self._compose_executable()
        if not compose_cmd:
            return DeployResult(
                ok=False, command="docker compose down", stdout="", stderr="",
                duration_ms=0, error="docker compose not found",
            )
        return self._run_cmd(compose_cmd + ["-f", compose_file, "down"])

    # ── Status ─────────────────────────────────────────────────────────────────

    def status(self, name: str = "meshflow") -> dict[str, Any]:
        """Return running container status."""
        if not shutil.which("docker"):
            return {"running": False, "error": "docker not found"}
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Status}}|{{.State.StartedAt}}|{{.NetworkSettings.Ports}}",
             name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"running": False, "name": name}
        parts = result.stdout.strip().split("|")
        return {
            "running": parts[0] == "running" if parts else False,
            "name": name,
            "state": parts[0] if parts else "unknown",
            "started_at": parts[1] if len(parts) > 1 else "",
        }

    # ── Internals ──────────────────────────────────────────────────────────────

    def _run_cmd(self, cmd: list[str]) -> DeployResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            ms = (time.monotonic() - t0) * 1000
            return DeployResult(
                ok=proc.returncode == 0,
                command=" ".join(cmd),
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=ms,
                error=proc.stderr if proc.returncode != 0 else "",
            )
        except subprocess.TimeoutExpired:
            ms = (time.monotonic() - t0) * 1000
            return DeployResult(
                ok=False, command=" ".join(cmd), stdout="", stderr="",
                duration_ms=ms, error=f"Command timed out after {self._timeout}s",
            )
        except Exception as exc:
            ms = (time.monotonic() - t0) * 1000
            return DeployResult(
                ok=False, command=" ".join(cmd), stdout="", stderr="",
                duration_ms=ms, error=str(exc),
            )

    @staticmethod
    def _find_dockerfile(context: str) -> str:
        for name in ("Dockerfile", "dockerfile"):
            path = os.path.join(context, name)
            if os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _compose_executable() -> list[str] | None:
        """Return ['docker', 'compose'] or ['docker-compose'] whichever is found."""
        # Try `docker compose` (Docker 20.10+ plugin)
        try:
            r = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                return ["docker", "compose"]
        except Exception:
            pass
        # Fall back to standalone docker-compose
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        return None
