#!/usr/bin/env bash
# End-to-end test of the sandbox.
#
# Builds the image (if not built), runs it, waits for /health, fires a
# small demo plan at /run, and prints the result. Cleans up the container
# afterward.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "Error: GEMINI_API_KEY not set" >&2
    exit 1
fi

IMAGE="agent-sandbox:latest"

echo "==> Building image (cached if no changes)"
docker build -f sandbox/Dockerfile -t "$IMAGE" .

echo "==> Starting container"
CID=$(docker run -d --rm \
    -p 6080:6080 -p 8000:8000 \
    -e GEMINI_API_KEY="$GEMINI_API_KEY" \
    --shm-size=2g \
    "$IMAGE")

cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Waiting for /health"
for _ in {1..30}; do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
        echo "    ready"
        break
    fi
    sleep 1
done

echo "==> Live view available at http://localhost:6080/vnc.html?autoconnect=1"
echo "==> Sending demo plan"

cat > /tmp/demo_plan.json <<'EOF'
{
  "plan": {
    "id": "plan_smoke",
    "goal": "Find Salesforce founding year",
    "steps": [
      {
        "id": "step_001",
        "kind": "navigate",
        "description": "Open Wikipedia",
        "details": {"url": "https://en.wikipedia.org/wiki/Salesforce"}
      },
      {
        "id": "step_002",
        "kind": "extract",
        "description": "Find the founding year of Salesforce",
        "details": {
          "variable_name": "founded_year",
          "description": "the year Salesforce was founded, shown in the infobox"
        }
      }
    ]
  },
  "max_steps": 6,
  "max_seconds": 120
}
EOF

curl -fsS -X POST http://localhost:8000/run \
    -H "Content-Type: application/json" \
    -d @/tmp/demo_plan.json | python3 -m json.tool

echo ""
echo "==> Done. Container ${CID:0:12} will be removed."