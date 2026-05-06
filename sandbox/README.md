# Sandbox

A self-contained Linux desktop in a Docker container. Receives a Plan via HTTP, executes it, returns the result. The desktop is viewable live via noVNC.

## Build

From the project root:

```bash
docker build -f sandbox/Dockerfile -t agent-sandbox:latest .
```

First build takes ~5 minutes (downloading Chromium + Playwright). Subsequent builds are fast thanks to layer caching.

## Run

```bash
docker run --rm -it \
  -p 6080:6080 -p 8000:8000 \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  --shm-size=2g \
  agent-sandbox:latest
```

- `-p 6080:6080` exposes noVNC. Open <http://localhost:6080/vnc.html?autoconnect=1> in your browser to watch the desktop.
- `-p 8000:8000` exposes the agent's HTTP API.
- `--shm-size=2g` is necessary because Chromium uses /dev/shm heavily; the default 64MB causes random crashes.

## Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","display":":99"}
```

## Execute a plan

The body of a /run POST is a `RunRequest`:

```json
{
  "plan": {
    "id": "plan_demo",
    "goal": "Find what year Salesforce was founded",
    "steps": [
      {
        "id": "step_001",
        "kind": "navigate",
        "description": "Open Wikipedia",
        "details": {"url": "https://en.wikipedia.org"}
      },
      {
        "id": "step_002",
        "kind": "ui_action",
        "description": "Search Wikipedia for Salesforce",
        "details": {
          "intent": "Type 'Salesforce' in the search box and submit",
          "target_description": "the Wikipedia search box at the top"
        }
      },
      {
        "id": "step_003",
        "kind": "extract",
        "description": "Find the year Salesforce was founded",
        "details": {
          "variable_name": "founded_year",
          "description": "the founding year shown on the Salesforce Wikipedia page"
        }
      }
    ]
  },
  "max_steps": 10,
  "max_seconds": 180
}
```

Save as `demo_plan.json`, then:

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d @demo_plan.json
```

While it runs, watch live at <http://localhost:6080/vnc.html?autoconnect=1>.

## Architecture

```
┌─ Docker container ────────────────────────────────────────┐
│                                                            │
│  Xvfb :99 (1440x900)  ◄── x11vnc:5900  ◄── novnc:6080 ────┼──► browser viewer
│       │                                                    │
│  Openbox (window manager)                                  │
│       │                                                    │
│  ┌─────┐   ┌─────────┐                                     │
│  │ ... │   │Chromium │  ◄── Playwright (browser_mode)      │
│  └─────┘   └─────────┘                                     │
│                                                            │
│  scrot + xdotool ◄── computer_mode (works on ANY app)      │
│                                                            │
│  FastAPI :8000                                             │
│    POST /run  ◄────────────────────────────────────────────┼──► backend
└────────────────────────────────────────────────────────────┘
```

## Modes

The executor picks per step:
- **Browser mode** (default for web steps): Playwright + DOM grounding. Fast, reliable for web.
- **Computer mode** (fallback or explicit): xdotool + scrot. Works on any app.

Plan authors can force a mode via `step.details.execution_mode = "browser"` or `"computer"`. Otherwise routing is automatic.

## Troubleshooting

- **Container exits immediately**: check `docker logs <id>` — usually a missing env var or `--shm-size` too small.
- **noVNC shows black screen**: Xvfb didn't start. Run `docker exec <id> cat /var/log/xvfb.err.log`.
- **/run hangs**: Chromium might be stuck — check `docker exec <id> cat /var/log/agent.log`.
- **Permission denied on entrypoint**: ensure `entrypoint.sh` was committed with executable bit (`git update-index --chmod=+x sandbox/entrypoint.sh`).