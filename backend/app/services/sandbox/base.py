"""
Sandbox runner abstraction.

The interface every cloud provider implements. Swap providers by changing
SANDBOX_RUNNER env var - no other code changes anywhere in the codebase.

Implementations:
  local_docker.py   subprocess-driven docker run         (today)
  modal.py          Modal cloud serverless containers    (stub - first user)
  fargate.py        AWS ECS Fargate                      (stub - enterprise)

Adding a new provider:
  1. Create new_provider.py with NewProviderRunner(SandboxRunner)
  2. Register in factory.py
  3. Set SANDBOX_RUNNER=new_provider in .env

That's it. Nothing else in the codebase needs to know.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxHandle:
    """An opaque handle returned by spawn(). The backend treats it as
    a black box; only the runner that created it knows how to talk to it.

    All providers fill these:
        sandbox_id       provider-internal identifier
        api_url          where to POST plans (the agent's HTTP endpoint)
        live_view_url    where the dashboard streams the desktop from

    Provider-specific bits go in `metadata` - never inspected outside
    the runner that created the handle.
    """
    sandbox_id: str
    api_url: str
    live_view_url: str
    runner_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpawnConfig:
    """What we tell the runner to provision."""
    image: str = "agent-sandbox:latest"
    env: dict[str, str] = field(default_factory=dict)
    cpu_units: int = 1024     # Fargate-style. local docker ignores.
    memory_mb: int = 2048
    shm_size_mb: int = 2048   # Chromium needs lots of /dev/shm
    timeout_seconds: int = 600
    # Dev only: mount a host path over /opt/sandbox_agent so sandbox code
    # changes are picked up without rebuilding the image.
    dev_mount: str | None = None


class SandboxRunner(ABC):
    """Interface every cloud provider implements."""

    runner_kind: str

    @abstractmethod
    async def spawn(self, config: SpawnConfig) -> SandboxHandle:
        """Provision a fresh sandbox container. Returns when the container
        is created (NOT necessarily when it's healthy - call wait_healthy)."""

    @abstractmethod
    async def wait_healthy(
        self, handle: SandboxHandle, *, timeout_seconds: int = 60
    ) -> bool:
        """Block until /health returns 200, or timeout. Returns True on
        success, False on timeout. Caller should teardown on False."""

    @abstractmethod
    async def execute_plan(
        self,
        handle: SandboxHandle,
        plan_dict: dict,
        *,
        max_steps: int = 50,
        max_seconds: int = 600,
        memory_hints: dict[str, str] | None = None,
    ) -> dict:
        """POST plan to the sandbox /run endpoint. Returns the run result."""

    @abstractmethod
    async def teardown(self, handle: SandboxHandle) -> None:
        """Destroy the container. Idempotent - calling on a dead container
        should not raise."""

    async def get_logs(self, handle: SandboxHandle) -> str:
        """Optional. For debugging when things go wrong. Default: empty."""
        return ""