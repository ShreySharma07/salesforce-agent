"""
Local Docker sandbox runner.

Spawns sandbox via `docker run`, polls /health, POSTs the plan, tears down.
For dev. NOT for production multi-user (no isolation between users beyond
the container boundary; ports collide if multiple sandboxes run at once).

Production = Modal or Fargate.
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import uuid

import httpx

from .base import SandboxHandle, SandboxRunner, SpawnConfig


def _find_free_port() -> int:
    """Get an unused TCP port in the 50060-55600 range.

    This range avoids:
      - Windows/Hyper-V admin exclusion   50000-50059
      - WSL2 ephemeral ports              60000+
      - Common Hyper-V dynamic exclusions 55619-56352 (seen on this host)
    socket.bind() only checks Linux availability; Docker exposes through
    Windows which enforces its own exclusion list, so we must stay below
    the first Windows-excluded block.
    """
    import random
    for _ in range(50):
        port = random.randint(50060, 55600)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("could not find a free port in 50060-55600 range")


class LocalDockerRunner(SandboxRunner):
    runner_kind = "local_docker"

    async def spawn(self, config: SpawnConfig) -> SandboxHandle:
        sandbox_id = f"sandbox_{uuid.uuid4().hex[:12]}"
        agent_port = _find_free_port()
        novnc_port = _find_free_port()

        # Build docker run command. Each -e flag passes one env var.
        cmd = [
            "docker", "run", "-d",
            "--name", sandbox_id,
            "-p", f"{agent_port}:8000",
            "-p", f"{novnc_port}:6080",
            f"--shm-size={config.shm_size_mb}m",
        ]
        for key, value in config.env.items():
            cmd += ["-e", f"{key}={value}"]
        if config.dev_mount:
            cmd += ["-v", f"{config.dev_mount}:/opt/sandbox_agent"]
        cmd.append(config.image)

        # Log the exact command we're running so we can replicate it manually
        import logging
        log = logging.getLogger("sandbox")
        log.info("Running: %s", " ".join(cmd))

        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {result.returncode}):\n"
                f"  cmd: {' '.join(cmd)}\n"
                f"  stdout: {result.stdout.strip()}\n"
                f"  stderr: {result.stderr.strip()}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(
                f"docker run returned empty container id. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        log.info("Spawned container: %s (id=%s)", sandbox_id, container_id[:12])

        return SandboxHandle(
            sandbox_id=sandbox_id,
            api_url=f"http://localhost:{agent_port}",
            live_view_url=f"http://localhost:{novnc_port}/vnc.html",
            runner_kind=self.runner_kind,
            metadata={
                "container_id": container_id,
                "agent_port": agent_port,
                "novnc_port": novnc_port,
            },
        )

    async def wait_healthy(
    self, handle: SandboxHandle, *, timeout_seconds: int = 60
    ) -> bool:
        """Poll /health until 200 OK or timeout. Catches the broad set of
        transient errors that occur during container startup (Docker forwards
        the port instantly but the HTTP server inside takes a few seconds)."""
        import logging
        log = logging.getLogger("sandbox")
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_error: str | None = None
        attempts = 0
        async with httpx.AsyncClient(timeout=3.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                attempts += 1
                try:
                    r = await client.get(f"{handle.api_url}/health")
                    if r.status_code == 200:
                        log.info("Sandbox %s healthy after %d attempts",
                                handle.sandbox_id, attempts)
                        return True
                    last_error = f"status={r.status_code}"
                except httpx.HTTPError as e:
                    # Catches ConnectError, ReadError, TimeoutException, RemoteProtocolError, etc.
                    last_error = f"{type(e).__name__}: {e}"
                await asyncio.sleep(1.0)
        log.warning(
            "Sandbox %s never healthy after %ds (%d attempts). Last: %s",
            handle.sandbox_id, timeout_seconds, attempts, last_error,
        )
        return False

    async def execute_plan(
        self,
        handle: SandboxHandle,
        plan_dict: dict,
        *,
        max_steps: int = 50,
        max_seconds: int = 600,
        memory_hints: dict[str, str] | None = None,
    ) -> dict:
        body = {
            "plan": plan_dict,
            "max_steps": max_steps,
            "max_seconds": max_seconds,
            "memory_hints": memory_hints or {},
        }
        # The executor checks the run budget BETWEEN steps, not during one.
        # A single step can consume its own per-step budget (≤ 600s by default)
        # even after the run budget is exhausted — so worst-case total is
        # max_seconds + max_seconds_per_step.  Add 120s for sandbox bookkeeping
        # (browser close, response serialisation).  Never let the HTTP client
        # race the sandbox to a timeout; the sandbox enforces its own limits.
        http_timeout = max_seconds + 600 + 120  # 600 = max per-step default
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            r = await client.post(f"{handle.api_url}/run", json=body)
            if r.status_code >= 400:
                raise RuntimeError(
                    f"sandbox /run returned {r.status_code}: {r.text[:2000]}"
                )
            return r.json()

    async def teardown(self, handle: SandboxHandle) -> None:
        # `docker rm -f` is idempotent enough for our needs - if the
        # container is already gone, this returns non-zero but we don't care.
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", handle.sandbox_id],
            capture_output=True,
            text=True,
        )

    async def get_logs(self, handle: SandboxHandle) -> str:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "logs", "--tail", "200", handle.sandbox_id],
            capture_output=True,
            text=True,
        )
        return (result.stdout or "") + (result.stderr or "")