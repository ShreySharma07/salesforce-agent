"""
Sandbox runner factory.

Reads SANDBOX_RUNNER env var, returns the corresponding implementation.
This is the ONLY place in the codebase that knows multiple providers exist.
Every caller does:

    from app.services.sandbox import get_sandbox_runner
    runner = get_sandbox_runner()
    handle = await runner.spawn(...)

Adding a new provider = add a branch here. Nothing else changes.
"""
from __future__ import annotations

from app.config import get_settings

from .base import SandboxRunner
from .fargate import FargateRunner
from .local_docker import LocalDockerRunner
from .modal import ModalRunner


def get_sandbox_runner() -> SandboxRunner:
    """Return the configured runner. Cheap, no caching needed - runners
    don't hold expensive state."""
    settings = get_settings()
    kind = settings.sandbox_runner.lower()

    if kind == "local_docker":
        return LocalDockerRunner()
    if kind == "modal":
        return ModalRunner()
    if kind == "fargate":
        return FargateRunner()

    raise ValueError(
        f"Unknown SANDBOX_RUNNER={kind!r}. "
        "Valid options: local_docker, modal, fargate"
    )