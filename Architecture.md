# Architecture

AI Work Automation Platform — a system that watches a screen recording of a
repetitive task once, generates an executable plan, and runs that plan
autonomously in an isolated cloud sandbox.

This document describes the system as of **Phase 2b** (ReAct executor loop).

---

## 1. The big picture

The system is split into **two processes** that run independently:

- **The backend** — a long-running Python/FastAPI service. The brain and the
  memory. Holds the database, the credential vault, and all business logic.
  Never touches a webpage directly.
- **The sandbox** — a Docker container, created fresh for every task run and
  destroyed afterward. The hands. Drives a real browser and desktop. Never
  holds a credential directly.

This separation is the core security principle of the project: the component
that touches untrusted web content (the sandbox) is never the component that
holds secrets (the backend). They communicate over HTTP, and the sandbox
authenticates with a per-run token that is useless outside its own run.

```
  User / CLI
      │
      ▼
  ┌─────────────────────────────────────────┐
  │ BACKEND (FastAPI)                        │
  │   api/  ─ thin HTTP layer                │
  │   agent/ ─ video → plan pipeline         │
  │   services/ ─ repo, vault, oauth, mcp    │
  │   db/  ─ SQLite (dev) / Postgres (prod)  │
  └─────────────────────────────────────────┘
      │ spawns + sends Plan          ▲ MCP calls
      ▼                              │ (per-run token)
  ┌─────────────────────────────────────────┐
  │ SANDBOX (Docker, per run)                │
  │   executor → ReAct loop / computer mode  │
  │   Chromium + virtual display + VNC       │
  └─────────────────────────────────────────┘
      │
      ▼
  External APIs (Gemini, Salesforce, Gmail)
```

---

## 2. Directory layout

```
salesforce-agent/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app; runs migrations on startup
│   │   ├── config.py          all settings, from environment variables
│   │   ├── api/               HTTP endpoints (thin glue)
│   │   │   ├── plans.py
│   │   │   ├── automations.py the /run endpoint lives here
│   │   │   ├── runs.py
│   │   │   ├── credentials.py
│   │   │   ├── oauth.py
│   │   │   └── mcp.py         sandbox calls back to this
│   │   ├── agent/             video → plan pipeline
│   │   │   ├── video_processor.py
│   │   │   ├── keyframe_captioner.py
│   │   │   └── plan_generator.py
│   │   ├── core/llm/          LLM provider abstraction
│   │   ├── db/                SQLAlchemy models + Alembic migrations
│   │   ├── schemas/           Pydantic models (the API contract)
│   │   └── services/          business logic
│   │       ├── run_repo.py    the single SqlRepo
│   │       ├── vault.py       Fernet-encrypted credential storage
│   │       ├── oauth/         OAuth 2.0 flow + token refresh
│   │       ├── mcp/           MCP servers (mock / salesforce / gmail)
│   │       └── sandbox/       sandbox runners (local_docker)
│   ├── scripts/               CLI tools
│   ├── alembic.ini
│   └── .env                   secrets + config (gitignored)
│
├── sandbox/                   Docker image definition
│   ├── Dockerfile
│   ├── supervisord.conf
│   └── entrypoint.sh
│
└── sandbox_agent/             code that runs INSIDE the container
    ├── main.py                FastAPI server on :8000
    ├── executor.py            walks the Plan, dispatches each step
    ├── browser_mode.py        the ReAct loop (Reason-Act-Observe)
    ├── computer_mode.py       xdotool desktop-control fallback
    ├── grounding.py           DOM element identification
    ├── llm_client.py          LLM calls from inside the sandbox
    ├── mcp_client.py          HTTP client → backend /mcp endpoint
    └── schemas.py             sandbox-side Pydantic models
```

---

## 3. The layers, bottom-up

### Configuration — `config.py`
One typed `Settings` object loaded from environment variables. Single source
of truth for the database URL, LLM provider, vault key, OAuth credentials,
and the public backend URL. Switching environments (dev → prod) is a config
change, never a code change.

### Database — `db/`
SQLAlchemy 2.0 async ORM. Six tables: `users`, `credentials`, `oauth_states`,
`plans`, `automations`, `runs`. Complex nested data (a Plan's steps, a Run's
step executions) is stored in JSON columns; the few fields used for queries
(status, user_id) are lifted into their own indexed columns. Alembic
migrations run automatically on backend startup, in a subprocess to avoid an
async-driver deadlock.

### Schemas — `schemas/`
Pydantic models defining the shape of data crossing the API. Kept separate
from the ORM models: schemas are the public contract, ORM models are storage.
The **Plan** is the master contract — every component either produces or
consumes a Plan.

### Services — `services/`
The real logic.
- **`run_repo.py`** — the single `SqlRepo`. All Plan/Automation/Run reads and
  writes go through it. The API layer never sees SQL.
- **`vault.py`** — encrypts secrets with Fernet before they touch the
  database. The encryption key lives only in an environment variable.
- **`oauth/`** — generic OAuth 2.0 Authorization Code flow plus automatic
  token refresh. One implementation serves Salesforce, Google, and Slack.
- **`mcp/`** — MCP servers, each wrapping an external API (Salesforce, Gmail)
  as typed callable tools. Plus a mock server for credential-free testing.
- **`sandbox/`** — spawns and tears down sandbox containers.

### Agent pipeline — `agent/`
Turns a screen recording into a Plan: `video_processor` (ffmpeg keyframes) →
`keyframe_captioner` (Gemini describes each frame) → `plan_generator`
(Gemini synthesizes a structured Plan).

### API — `api/`
Thin HTTP glue. Each file is one FastAPI router that validates input and
delegates to a service. The most important endpoint is
`POST /automations/{id}/run`.

---

## 4. The sandbox

Spawned fresh per run, destroyed after. Inside the container:

- **Display stack** — Xvfb (a virtual screen), x11vnc + noVNC (stream that
  screen so a human can watch the agent live in a browser tab),
  supervisord (keeps all processes alive).
- **`executor.py`** — receives the Plan, walks its steps, dispatches each by
  `kind`.
- **`browser_mode.py`** — the **ReAct loop**. For UI steps, runs
  Reason → Act → Observe cycles (see §6).
- **`computer_mode.py`** — fallback that controls the whole desktop with
  xdotool when browser mode is stuck.
- **`mcp_client.py`** — calls the backend's `/mcp` endpoint for `mcp_call`
  steps, authenticating with the per-run token.

The sandbox never holds an OAuth token. For anything credentialed, it asks
the backend.

---

## 5. End-to-end flow

**Record → Plan.** `process_video.py` → ffmpeg keyframes → Gemini captions →
Gemini synthesizes a Plan → saved to the database via `SqlRepo`.

**Trigger → Run.** `POST /automations/{id}/run` loads the Plan, creates a Run
record, generates a random `RUN_TOKEN` (stores only its hash), spawns a
sandbox container with `RUN_ID`, `RUN_TOKEN`, and the backend URL injected,
then POSTs the Plan to the sandbox.

**Execute.** The executor walks the steps. UI steps run the ReAct loop
driving Chromium. `mcp_call` steps call back to the backend. A human can
watch live over VNC.

**Credentialed calls.** For an `mcp_call`, the sandbox's `mcp_client` calls
the backend's `/mcp/{server}/{tool}` with the `RUN_TOKEN`. The backend
validates the token, resolves which user the run belongs to, fetches that
user's credentials from the vault (refreshing if expired), and calls the
real API. The token never enters the sandbox.

**Finish.** The sandbox returns a RunResponse with every step's result and
the full ReAct trace. The backend saves it, tears down the container, and
the result is available at `GET /runs/{id}`.

---

## 6. The ReAct loop (Phase 2b)

A UI step is treated as a **goal**, not a fixed instruction. The loop
repeats:

1. **Observe** — wait for the page to stabilize (`wait_for_stable`), capture
   a screenshot and the list of interactive elements.
2. **Reason** — send Gemini the goal, the current screenshot, and the
   *entire trajectory* of past (thought, action, observation) for this step.
   Gemini returns one action.
3. **Act** — perform the action via Playwright.

It repeats until the goal is met, the agent gives up, a CAPTCHA is detected
(the loop pauses for a human — it never tries to solve one), or an
iteration / wall-time budget is exhausted. Every cycle is recorded into a
trace, persisted on the Run, and readable afterward. Feeding the full
trajectory back each turn is what stops the agent repeating a failed
approach — the core of "agentic" behaviour.

---

## 7. Security model

- **Zero-trust sandbox.** The component touching untrusted web content never
  holds long-lived credentials.
- **Per-run token.** Each run gets a random `RUN_TOKEN`; only its hash is
  stored. The backend validates it on every MCP call, so a compromised
  sandbox can act only for its own run.
- **Encrypted vault.** OAuth tokens and API keys are Fernet-encrypted at
  rest; the key lives only in an environment variable.
- **Known gaps (tracked, not yet fixed):** the Gemini API key is currently
  passed to the sandbox as an environment variable — it should be proxied
  through the backend like MCP calls are. The live-view (VNC) has no auth.
  The sandbox has no egress restriction. See the security backlog.

---

## 8. Design principles

- **The Plan is the contract.** Every component produces or consumes a Plan;
  changing one component doesn't ripple to others.
- **One implementation per concern.** Interfaces exist to allow swapping
  (LLM provider, sandbox runner, database), but exactly one implementation
  is active — no dead alternatives.
- **No layer reaches around another.** The API never touches SQL directly;
  the sandbox never touches a credential. That discipline is what keeps the
  system sound.
- **Config, not code, changes between environments.** Deploying is setting
  environment variables, not editing source.