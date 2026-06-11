"""
AWS ECS Fargate sandbox runner. STUB.

Pick this when you need:
  - Enterprise customer requirements (data residency, VPC isolation)
  - Per-tenant network policies (egress firewall is easier on AWS)
  - Existing AWS infrastructure (RDS, S3, CloudWatch already in place)

Modal is simpler for early stages. Move to Fargate when scale or compliance
demand it.

To activate:
  1. Build & push image:
        aws ecr get-login-password | docker login --username AWS ...
        docker tag agent-sandbox:latest <ecr-uri>
        docker push <ecr-uri>
  2. Create ECS cluster + task definition (Terraform recipe in
     infra/terraform/fargate.tf — to be written when ready)
  3. Set SANDBOX_RUNNER=fargate + AWS env vars in backend/.env
  4. Implement the methods below.

Sketch of the real implementation:

    import boto3
    ecs = boto3.client("ecs")

    class FargateRunner(SandboxRunner):
        async def spawn(self, config):
            response = await asyncio.to_thread(
                ecs.run_task,
                cluster="agent-sandbox-cluster",
                taskDefinition="agent-sandbox:latest",
                launchType="FARGATE",
                networkConfiguration={...},        # VPC + security group
                overrides={"containerOverrides": [{
                    "name": "agent-sandbox",
                    "environment": [{"name": k, "value": v}
                                    for k, v in config.env.items()],
                }]},
            )
            task_arn = response["tasks"][0]["taskArn"]
            # Resolve the task's ENI public IP, or use service discovery
            ip = await self._get_task_ip(task_arn)
            return SandboxHandle(
                sandbox_id=task_arn,
                api_url=f"http://{ip}:8000",
                live_view_url=f"http://{ip}:6080/vnc.html",
                runner_kind="fargate",
                metadata={"task_arn": task_arn, "ip": ip},
            )
        ...

Note: for production you'd probably put an ALB in front of the tasks
instead of relying on task IPs, and use AWS WAF + a per-tenant security
group for the egress firewall layer.
"""
from __future__ import annotations

from .base import SandboxHandle, SandboxRunner, SpawnConfig


_NOT_IMPLEMENTED_MSG = (
    "FargateRunner is a stub. To activate AWS Fargate sandboxes, see "
    "backend/app/services/sandbox/fargate.py — the docstring contains the "
    "implementation recipe."
)


class FargateRunner(SandboxRunner):
    runner_kind = "fargate"

    async def spawn(self, config: SpawnConfig) -> SandboxHandle:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def wait_healthy(self, handle, *, timeout_seconds=60) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def execute_plan(self, handle, plan_dict, *, max_steps=50, max_seconds=600, memory_hints=None) -> dict:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def teardown(self, handle) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)