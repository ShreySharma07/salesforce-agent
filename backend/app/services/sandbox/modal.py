"""
Modal cloud sandbox runner. STUB.

Modal is a serverless container platform that's particularly good for
agent workloads — sub-2-second cold starts, per-second billing, free $30/mo
credit on signup. Strongly recommended as the first cloud destination
when you onboard real users.

To activate:
  1. pip install modal
  2. modal token new                       (one-time auth)
  3. Deploy the sandbox image to Modal:
        modal deploy infra/modal/deploy.py (file to be written when ready)
  4. Set SANDBOX_RUNNER=modal in backend/.env
  5. Replace the NotImplementedError stubs below with the implementation
     sketched in the comments.

Sketch of the real implementation (when you're ready):

    import modal

    image = modal.Image.from_registry("ghcr.io/yourorg/agent-sandbox:latest")
    app = modal.App("agent-sandbox-runner")

    @app.function(image=image, cpu=1.0, memory=2048, timeout=600,
                  allow_concurrent_inputs=1)
    @modal.web_endpoint(method="POST")
    def run_endpoint(plan_dict: dict):
        # Forward to the in-container agent
        ...

    class ModalRunner(SandboxRunner):
        async def spawn(self, config):
            handle_id = uuid.uuid4().hex
            f = run_endpoint.spawn(...)        # creates a container
            return SandboxHandle(
                sandbox_id=handle_id,
                api_url=f.web_url,             # Modal-issued tunnel URL
                live_view_url=f"{f.web_url}/vnc.html",
                runner_kind="modal",
                metadata={"function_id": f.object_id},
            )
        ...

Until then, attempting to use this runner raises a clear error pointing
operators at this docstring.
"""
from __future__ import annotations

from .base import SandboxHandle, SandboxRunner, SpawnConfig


_NOT_IMPLEMENTED_MSG = (
    "ModalRunner is a stub. To activate Modal cloud sandboxes, see "
    "backend/app/services/sandbox/modal.py — the docstring contains the "
    "implementation recipe. For now, set SANDBOX_RUNNER=local_docker."
)


class ModalRunner(SandboxRunner):
    runner_kind = "modal"

    async def spawn(self, config: SpawnConfig) -> SandboxHandle:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def wait_healthy(self, handle, *, timeout_seconds=60) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def execute_plan(self, handle, plan_dict, *, max_steps=50, max_seconds=600, memory_hints=None) -> dict:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def teardown(self, handle) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)