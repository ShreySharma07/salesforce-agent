# Architecture

How the AI Work Automation Agent is built — the design decisions, the data
flow, and the security model. Current as of the `open_app` / on-demand-login
milestone.

---

## 1 · The core idea

The system has **two processes** separated by a hard trust boundary:

- **🧠 Backend** — a long-running FastAPI service. The *brain and the vault*.
  Holds the database, the encrypted credentials, and all business logic.
  **Never touches a webpage.**
- **🦾 Sandbox** — a Docker container, spawned fresh for every run and
  destroyed after. The *hands*. Drives a real Chromium browser.
  **Never holds a credential or an API key** — it authenticates back to the
  backend with a random, scoped, per-run token.

> The component that touches untrusted web content is never the component
> that holds secrets. This one principle shapes the entire design.

```
        ┌──────────────────────────────────────────────┐
        │  BACKEND  (brain + vault)                      │
        │                                                │
        │   api/        thin HTTP layer                  │
        │   agent/      video → plan pipeline            │
        │   services/   repo · vault · oauth · mcp ·     │
        │               llm-proxy · frontdoor · runner   │
        │   db/         SQLite (dev) / Postgres (prod)   │
        └──────────────────────────────────────────────┘
            │ spawn + Plan            ▲ LLM + tool calls
            │ (inject RUN_TOKEN)      │ (Bearer RUN_TOKEN)
            ▼                         │
        ┌──────────────────────────────────────────────┐
        │  SANDBOX  (hands — one per run)                │
        │                                                │
        │   executor → ReAct loop → Chromium             │
        │   llm_client · mcp_client  (no secrets held)   │
        └──────────────────────────────────────────────┘
            │
            ▼   🌐 Gemini      ☁️ Salesforce
```

---

## 2 · The journey of a task

### 2.1 — Recording becomes a Plan

```
screen recording → [video_processor] → keyframes (ffmpeg)
                 → [keyframe_captioner] → per-frame descriptions (Gemini)
                 → [plan_generator] → structured Plan → saved to DB
```

The **Plan is the master contract** of the whole system. Everything upstream
produces it; everything downstream consumes it. A Plan is a list of typed
**Steps** (`navigate`, `ui_action`, `extract`, `mcp_call`, `wait`, …), each
with an `on_failure` policy.

### 2.2 — Plan becomes a Run

```
POST /automations/{id}/run
   → create Run row
   → mint RUN_TOKEN (store only its SHA-256 hash on the Run)
   → spawn sandbox, inject: RUN_TOKEN, BACKEND_MCP_URL, RUN_ID,
                            and per-connected-provider frontdoor paths
   → POST the Plan to the sandbox's /run endpoint
```

### 2.3 — The Run executes

Inside the sandbox, the **executor** walks the Plan and dispatches each step
by kind:

- `navigate` / `wait` → handled directly
- `mcp_call` → straight to the backend MCP endpoint (no LLM cost)
- `ui_action` / `extract` → the **ReAct loop** (see §3)

When the run ends, the sandbox returns a `RunResponse` carrying every step's
result **and its full reasoning trace**. The backend persists it and tears
the container down.

---

## 3 · The ReAct loop — what makes it agentic

A UI step is treated as a **goal**, not a fixed instruction. The loop repeats:

```
   ┌─────────────────────────────────────────────┐
   │  OBSERVE   wait for the page to settle,       │
   │            screenshot + extract elements      │
   │     │                                         │
   │     ▼                                         │
   │  REASON    send Gemini the goal, the screen,  │
   │            and the FULL trajectory so far     │
   │     │                                         │
   │     ▼                                         │
   │  ACT       perform one chosen action          │
   │     │                                         │
   │     └──────────── loop ◀──────────────────────┤
   └─────────────────────────────────────────────┘
        ends on: done · give_up · captcha · budget
```

Key design choices:

- **Full-trajectory memory** — every turn the agent sees all prior
  (thought, action, observation). This is what stops it repeating a failed
  approach. (Past *screenshots* are referenced, not re-embedded, to bound
  token cost.)
- **Wait-for-stable before every Observe** — most "screenshotted mid-render"
  flake is eliminated here, not in the reasoning.
- **A rich action vocabulary** — `click`, `fill`, `navigate`, `scroll`,
  `dismiss_obstruction` (clear a popup), `open_app` (enter a connected app
  logged in — see §5), `captcha_detected` (pause for a human, never solve),
  `done`, `give_up`.
- **Hard budgets** — per-step max iterations and wall-time, so a confused
  agent can't loop forever or burn unbounded LLM calls.
- **Every iteration is traced** — `{thought, action, observation,
  screenshot_ref, latency, tokens}` is recorded and persisted. This is both
  the debugging tool and the evidence that the agent genuinely reasons.

---

## 4 · The security model

### 4.1 — Zero-trust sandbox
The sandbox is the only thing touching untrusted web pages, so it's the one
thing that holds **no long-lived secrets**. It gets a single `RUN_TOKEN`
(random, hashed-on-the-Run, useless outside its own run) and nothing else.

### 4.2 — The LLM proxy
The sandbox never holds the LLM API key. Its `llm_client` is a thin HTTP
client that calls the backend's `/sandbox/llm/generate`, authenticating with
the `RUN_TOKEN`. The backend makes the real Gemini call with *its* key.

```
sandbox.llm_client ──(RUN_TOKEN)──▶ backend /sandbox/llm ──(API key)──▶ Gemini
```

A `printenv` inside the container reveals no API key. The key lives in
exactly one place.

### 4.3 — The credential vault
OAuth tokens and API keys are **Fernet-encrypted** before they touch the
database. The encryption key lives only in `VAULT_ENCRYPTION_KEY` (env var,
never in the DB). Non-secret metadata (`instance_url`, `scope`) is stored
in the clear so the system can show connection status without decrypting.

### 4.4 — Per-run token validation
Every sandbox → backend call (`/mcp`, `/sandbox/llm`, `/sandbox/frontdoor`)
carries the `RUN_TOKEN` as a bearer credential. The backend hashes it and
compares to the hash stored on the Run. A compromised sandbox can act only
for its own run — it cannot swap a run_id to reach another user's data.

---

## 5 · Connecting to Salesforce — OAuth + `singleaccess`

This is the part that lets the agent operate a *logged-in* Salesforce UI
without ever holding the Salesforce token.

### 5.1 — One-time connection (OAuth)

```
You → /oauth/salesforce/connect → Salesforce login → click Allow
   → Salesforce redirects the code to YOUR /oauth/salesforce/callback
   → backend exchanges code for tokens
   → tokens encrypted into the vault
```

The callback **must** reach the backend — that's the whole reason a
registered callback URL is required. (A third-party callback like Postman's
can't work: the code would land somewhere the backend can't read, so the
vault would stay empty.)

### 5.2 — Per-run login (`singleaccess`)

When the agent decides it needs Salesforce, it emits the **`open_app`**
action. The system turns that into a logged-in session — lazily, on demand,
without the sandbox ever seeing the token:

```
agent: open_app "salesforce"
   → sandbox navigates to backend /sandbox/frontdoor/salesforce?run_token=…
   → backend validates RUN_TOKEN, resolves the user
   → reads the Salesforce token from the vault (refreshing if expired)
   → calls Salesforce POST /services/oauth2/singleaccess  (token stays server-side)
   → Salesforce returns a ONE-TIME login URL
   → backend 302-redirects the sandbox browser to it
   → browser lands in a logged-in Salesforce session
```

The raw token exists only in the vault and in the backend's memory during
the `singleaccess` call. The browser only ever follows a **single-use**
URL — worthless after one use. This is why `singleaccess` is used instead of
the older `frontdoor.jsp?sid=<token>` form, which would expose the raw token
in a URL.

**Auth is the system's job; navigation is the agent's job.** `open_app` gets
the agent *into* Salesforce logged in; from there the agent navigates the
Lightning UI itself by understanding it — no hardcoded URLs.

---

## 6 · Reliability

- **Honors `on_failure`** — a failed step is routed by its declared policy:
  `abort` stops the run, `pause` halts for human review (so a broken
  prerequisite never lets a dependent step run), `continue` proceeds.
- **Transient-error retries** — the LLM proxy retries 429 (rate), 503
  (overload), and the occasional bad-image blip, with backoff.
- **Quota circuit-breaker** — *daily* quota exhaustion is detected and fails
  fast (no pointless retry sleeps), and aborts the whole run immediately
  rather than marching every remaining step into the same wall.

---

## 7 · Directory map

```
backend/app/
  main.py            FastAPI app; runs migrations on startup
  config.py          single typed Settings object (12-factor, env-driven)
  api/               thin HTTP routers, one concern each
  agent/             video → plan pipeline
  db/                SQLAlchemy 2.0 async models + Alembic migrations
  schemas/           Pydantic models — the API contract
  services/
    run_repo.py      the single SqlRepo (all DB access goes through it)
    vault.py         Fernet-encrypted credential storage
    oauth/           OAuth 2.0 flow + automatic token refresh
    mcp/             MCP servers wrapping external APIs as tools
    frontdoor.py     Salesforce singleaccess URL builder
    sandbox/         sandbox runners (local_docker)

sandbox_agent/       (runs INSIDE the container)
  main.py            sandbox HTTP server (/run)
  executor.py        walks the Plan, dispatches each step
  browser_mode.py    the ReAct loop
  computer_mode.py   xdotool desktop fallback
  llm_client.py      → backend LLM proxy
  mcp_client.py      → backend MCP endpoint
```

---

## 8 · Design principles

- **The Plan is the contract.** Components couple to the Plan, not to each
  other — change one without rippling the rest.
- **One implementation per concern.** Interfaces exist to allow swapping
  (LLM provider, sandbox runner, DB), but exactly one is active — no dead
  alternatives carried as debt.
- **No layer reaches around another.** The API never touches SQL directly;
  the sandbox never touches a credential. That discipline is what keeps the
  system sound as it grows.
- **Config, not code, changes between environments.** Dev → prod is setting
  environment variables (`PUBLIC_BACKEND_BASE_URL`, `DATABASE_URL`, …), never
  editing source.

---

## 9 · Known limitations (honest list)

- **Single-user.** Everything runs as one default user today; real
  multi-user auth and per-user data scoping are not yet built.
- **No frontend yet.** Interaction is via CLI scripts and curl; a dashboard
  is on the roadmap.
- **Plans are create-only.** No update/versioning endpoint yet.
- **Free-tier LLM is a real constraint.** Iteration speed is gated by the
  20-calls/day free Gemini cap until billing is enabled.
- **CAPTCHA is an explicit non-goal.** The agent pauses and hands off; it
  never attempts to solve one.