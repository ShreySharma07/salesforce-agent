# RAI Work Automation Agent Platform

An autonomous agent platform that watches a screen recording of a repetitive
task once, generates an executable plan, and runs that plan on its own inside
an isolated cloud sandbox. The agent uses a **ReAct (Reason–Act–Observe)
loop** to perceive a live browser, reason over screenshots, and adapt its
actions in real time.

The initial wedge is Salesforce data-hygiene tasks (creating and updating
leads/contacts), but the architecture is general — any web UI task.

---

## What it does

1. You record yourself doing a task once (a screen recording).
2. The platform turns that recording into a structured **Plan**.
3. You trigger the Plan, it runs autonomously in a fresh, isolated sandbox.
4. The agent drives a real browser, reasoning step by step, and can call
   external services (e.g. Salesforce) through a secure tool layer.
5. You can watch it work live, and inspect a full reasoning trace afterward.

---

## Architecture at a glance

The system is split into two processes with a strict trust boundary:

- **Backend** — the brain and memory. Holds the database, the encrypted
  credential vault, and all business logic. Never touches a webpage directly.
- **Sandbox** — a Docker container, spawned fresh per run, destroyed after.
  Drives a real browser. **Never holds credentials or API keys** — it
  authenticates back to the backend with a scoped, per-run token.

```
  User / CLI
      |
      v
  +-------------------------------------------+
  | BACKEND (FastAPI)                         |
  |   api/      thin HTTP layer               |
  |   agent/    video -> plan pipeline        |
  |   services/ repo, vault, oauth, mcp,      |
  |             llm proxy, frontdoor, runner  |
  |   db/       SQLite (dev) / Postgres (prod)|
  +-------------------------------------------+
      | spawns + sends Plan        ^ LLM + tool calls
      v                            | (per-run token)
  +-------------------------------------------+
  | SANDBOX (Docker, per run)                 |
  |   executor -> ReAct loop -> Chromium      |
  |   LLM proxy client, MCP client            |
  +-------------------------------------------+
      |
      v
  External APIs (Gemini, Salesforce)
```

Two security properties worth calling out:

- **LLM key never enters the sandbox.** The sandbox's LLM calls are proxied
  through the backend (`/sandbox/llm/generate`) the API key lives only on
  the backend.
- **Salesforce token never enters the sandbox.** The agent logs into
  Salesforce via the OAuth2 `singleaccess` endpoint, which mints a one-time
  login URL on the backend — the raw token stays in the vault.

See `ARCHITECTURE.md` for the full design.

---

## Prerequisites

- **Python 3.12**
- **Docker Desktop** (running)
- **ffmpeg** (for video keyframe extraction): `brew install ffmpeg` on macOS
- A **Google Gemini API key** (free tier works for light use; paid recommended
  for real iteration — the free tier is capped at 20 calls/day per project)
- (Optional, for Salesforce features) A **Salesforce org** you control — a free
  Developer Edition org from developer.salesforce.com works perfectly

---

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url> salesforce-agent
cd salesforce-agent/backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .          # or: pip install -r requirements.txt
```

### 2. Configure environment

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

Required values in `backend/.env`:

```
# LLM
GEMINI_API_KEY=<your gemini api key>
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash

# Vault encryption key — generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
VAULT_ENCRYPTION_KEY=<generated key>

# How the outside world reaches this backend (used for OAuth callbacks)
PUBLIC_BACKEND_BASE_URL=http://localhost:8001
```

Optional — Salesforce (only if using Salesforce features), see step 6:

```
SALESFORCE_CLIENT_ID=<connected app consumer key>
SALESFORCE_CLIENT_SECRET=<connected app consumer secret>
SALESFORCE_AUTH_URL=https://login.salesforce.com
```

**Important:** do NOT also export `GEMINI_API_KEY` in your shell (`.zshrc`
etc.). A shell-exported variable overrides `.env` and causes confusing
"wrong key" bugs. Keep the key only in `.env`.

### 3. Build the sandbox Docker image

```bash
# from the repo root (not backend/)
cd ..
docker build -t agent-sandbox:latest -f sandbox/Dockerfile .
```

Confirm it built correctly:

```bash
docker image inspect agent-sandbox:latest > /dev/null 2>&1 && echo FOUND || echo MISSING
```

Must print `FOUND`. (Re-tag with `docker tag <image-id> agent-sandbox:latest`
if the name came out wrong.)

### 4. Run the backend

```bash
cd backend
python -m app.main
```

Migrations run automatically on startup. You should see `Backend up. ...`
and the server listening on `http://localhost:8001`.

Health check:

```bash
curl -s http://localhost:8001/health | python -m json.tool
```

`vault_configured` should be `true`.

### 5. Run a demo plan (no credentials needed)

```bash
cd backend
python -m scripts.run_plan_e2e .local_storage/plans/plan_arxiv_demo.json --watch
```

A `watch:` URL is printed — open it to watch the agent live (noVNC). When the
run finishes, inspect it:

```bash
curl -s http://localhost:8001/runs/<run_id> | python -m json.tool
```

Look at `step_executions[].trace` to see the agent's reasoning step by step.

### 6. (Optional) Connect Salesforce

This is a one-time account connection. You only do it once; afterward the
platform keeps the token fresh on its own.

**a. Create a Connected App** in your Salesforce org:
   - Setup -> App Manager -> New Connected App
   - Enable OAuth Settings
   - Callback URL: `http://localhost:8001/oauth/salesforce/callback`
   - OAuth Scopes: add **Full access (full)**, **Manage user data via APIs
     (api)**, and **Perform requests at any time (refresh_token,
     offline_access)**
   - Save, then wait ~10 minutes for it to propagate

**b. Copy the Consumer Key + Secret** (Manage Consumer Details) into
   `backend/.env` (`SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`).
   Restart the backend.

**c. Connect:**
```bash
curl -s http://localhost:8001/oauth/providers | python -m json.tool
#   salesforce should show "configured": true
```
   Then open `http://localhost:8001/oauth/salesforce/connect` in a browser,
   log in, and click Allow. You should land on a green success page.

**d. Verify:**
```bash
curl -s http://localhost:8001/oauth/providers | python -m json.tool
#   salesforce should now show "connected": true
```

The Salesforce token is now encrypted in the vault. Plans that use Salesforce
will work from now on without re-logging-in.

---

## Project layout

```
salesforce-agent/
  backend/
    app/
      main.py            FastAPI app; runs migrations on startup
      config.py          all settings, from environment variables
      api/               HTTP endpoints (plans, automations, runs,
                         credentials, oauth, mcp, sandbox_llm,
                         sandbox_frontdoor)
      agent/             video -> plan pipeline
      db/                SQLAlchemy models + Alembic migrations
      schemas/           Pydantic models (the API contract)
      services/          business logic (repo, vault, oauth, mcp,
                         frontdoor, sandbox runner)
    scripts/             CLI tools (process_video, run_plan_e2e, ...)
    .env                 secrets + config (gitignored)
  sandbox/               Docker image definition
  sandbox_agent/         code that runs INSIDE the container
    main.py              sandbox HTTP server (/run)
    executor.py          walks the Plan, dispatches each step
    browser_mode.py      the ReAct loop (Reason-Act-Observe)
    computer_mode.py     xdotool desktop-control fallback
    llm_client.py        HTTP client to the backend LLM proxy
    mcp_client.py        HTTP client to the backend MCP endpoint
    schemas.py           sandbox-side Pydantic models
  ARCHITECTURE.md        full architecture document
```

---

## Common commands

```bash
# Backend (from backend/, in a shell where GEMINI_API_KEY is NOT exported)
python -m app.main

# Free port 8001 if a stale backend is holding it
lsof -ti:8001 | xargs kill -9

# Rebuild the sandbox image (REQUIRED after editing anything in sandbox_agent/)
docker build --no-cache -t agent-sandbox:latest -f sandbox/Dockerfile .

# Run a plan end-to-end with live view
python -m scripts.run_plan_e2e .local_storage/plans/<plan>.json --watch

# Inspect a run + its reasoning trace
curl -s http://localhost:8001/runs/<run_id> | python -m json.tool
```

---

## Troubleshooting

- **`address already in use` on startup** — a stale backend holds the port:
  `lsof -ti:8001 | xargs kill -9`, then restart.
- **Wrong / old Gemini key being used** — you have `GEMINI_API_KEY` exported
  in your shell, which overrides `.env`. Remove it from `~/.zshrc`, open a
  fresh terminal (`echo $GEMINI_API_KEY` should be blank), restart the backend.
- **`Sandbox image not found` warning** — the image name is wrong or the
  backend started before the build. Verify with
  `docker image inspect agent-sandbox:latest`; re-tag or rebuild, then restart.
- **Changes to `sandbox_agent/` files don't take effect** — you must rebuild
  the Docker image (`docker build --no-cache ...`). The backend you can just
  restart; the sandbox you must rebuild.
- **"Salesforce not connected" despite being connected** — the data lives in
  the SQLite DB, not the `.local_storage/*.json` files. Editing files does
  nothing, the API reads the DB. Use the API/DB as the source of truth.
- **Out of LLM quota / runs aborting** — the free Gemini tier is 20 calls/day
  per project. A multi-step run can exhaust it. Enable billing for real
  iteration.

---

## Status

Working and verified:
- Video -> plan pipeline
- ReAct executor loop with full reasoning trace + cost tracking
- Isolated Docker sandbox with live (noVNC) view
- LLM proxy (sandbox holds no API key)
- Per-run token auth for sandbox -> backend calls
- Encrypted credential vault + OAuth (Salesforce)
- Salesforce `singleaccess` FrontDoor (one-time login URL)

In progress:
- Agent-initiated "open Salesforce" action (lazy, on-demand login mid-plan)
- Reliability hardening (transient-error retries, quota circuit breaker)

Roadmap:
- Multi-user / authentication (currently single default user)
- Web dashboard (upload, plan review, run history)
- Scheduling and triggers
```