# Architecture

How the AI Work Automation Agent is built — the design decisions, the data
flow, the learning loop, and the security model.

---

## 1 · The core idea

The system has **two processes** separated by a hard trust boundary:

- **🧠 Backend** — a long-running FastAPI service. The *brain and the vault*.
  Holds the database, the encrypted credentials, the memory stores, and all
  business logic. **Never touches a webpage.**
- **🦾 Sandbox** — a Docker container, spawned fresh for every run and
  destroyed after. The *hands*. Drives a real Chromium browser.
  **Never holds a credential or an API key** — it authenticates back to the
  backend with a random, scoped, per-run token.

> The component that touches untrusted web content is never the component
> that holds secrets. This one principle shapes the entire design.

```
   You / CLI / Web dashboard
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │  BACKEND  (brain + vault)                      │
   │                                                │
   │   api/        thin HTTP layer                  │
   │   agent/      video → plan pipeline            │
   │   services/   repo · vault · oauth · mcp ·     │
   │               memory · llm-proxy · runner      │
   │   core/       guardrails · budget · prompts    │
   │   db/         SQLite (dev) / Postgres (prod)   │
   └──────────────────────────────────────────────┘
        │ spawn + Plan            ▲ LLM + tool calls
        │ (inject RUN_TOKEN,      │ (Bearer RUN_TOKEN)
        │  memory hints)          │
        ▼                         │
   ┌──────────────────────────────────────────────┐
   │  SANDBOX  (hands — one per run)                │
   │                                                │
   │   executor → step dispatch → Chromium          │
   │     ├─ sequence steps  (deterministic)         │
   │     └─ ReAct loop      (LLM-driven)            │
   │   llm_client · mcp_client  (no secrets held)   │
   └──────────────────────────────────────────────┘
        │
        ▼   🌐 Gemini      ☁️ Salesforce
```

---

## 2 · The journey of a task (stepwise)

### 2.1 — Recording becomes a Plan

```
screen recording → [video_processor]     → keyframes (ffmpeg)
                 → [audio_transcriber]    → narration timeline (optional)
                 → [keyframe_captioner]   → per-frame descriptions (Gemini vision)
                 → [plan_generator]       → structured Plan → saved to DB
```

The **Plan is the master contract** of the whole system. Everything upstream
produces it; everything downstream consumes it. A Plan is an ordered list of
typed **Steps**, each with a `success_condition` (for state-changing steps)
and an `on_failure` policy (`pause` · `skip` · `abort` · `retry`).

### 2.2 — Plan becomes a Run

```
POST /automations/{id}/run
   → create Run row
   → mint RUN_TOKEN (store only its SHA-256 hash on the Run)
   → MEMORY: prime_steps() — attach per-step hints from past runs
   → spawn sandbox, inject: RUN_TOKEN, BACKEND_MCP_URL, RUN_ID,
                            memory hints, per-provider frontdoor paths
   → POST the Plan to the sandbox's /run endpoint
```

### 2.3 — The Run executes

Inside the sandbox, the **executor** walks the Plan and dispatches each step by
kind:

- `navigate` / `wait` → handled directly, no LLM
- `mcp_call` → straight to the backend MCP endpoint (no LLM cost)
- `sequence` → **deterministic ordered sub-actions** (see §3)
- `ui_action` / `extract` / `decision` → the **ReAct loop** (see §4)
- `loop` → drains a collection, re-dispatching its body per item

When the run ends, the sandbox returns a `RunResponse` carrying every step's
result **and its full reasoning trace**. The backend persists it, runs
`reflect_after_run()` to learn from the run (see §5), then tears the container
down.

### 2.4 — The learning loop

```
   ┌──────────── prime_steps ──────────┐
   │ (before run: recall what worked)   │
   ▼                                    │
[ RUN ] ── step traces + outcomes ──▶ reflect_after_run
                                        (after run: distill + capture + reflect)
                                              │
                                              ▼
                                     procedural · episodic · lessons stores
                                              │
                                              └──── feeds the next run ◀──┘
```

---

## 3 · Two ways to do a step — deterministic vs agentic

Not every step needs an LLM. The executor supports two execution styles, and
choosing the right one is the biggest lever on speed and reliability.

### 3.1 — `sequence` steps (deterministic)

A `sequence` step is an **ordered list of sub-actions run without any LLM
in the loop**. Used for well-understood UI patterns like Salesforce inline
field edits. Sub-action kinds:

| Sub-action | What it does |
|---|---|
| `click_pencil_icon` | Enter inline-edit mode for a named field |
| `fill_field` | Type into a field's own input using real keyboard events (fires SOQL) |
| `click_dropdown_result` | Poll for the matching lookup option and click it → linked-record pill |
| `select_dropdown_option` | Pick a value from a picklist dropdown |
| `click_save_footer` | Click the inline-edit Save button |

A `sequence` step is idempotent for free: before running, the executor checks
its `success_condition` with a lightweight Playwright query (no LLM) and
self-skips if already satisfied. In practice these steps run **~5–7× faster**
than the LLM path and never "wander."

### 3.2 — The ReAct loop (agentic) — §4

When the target UI is unknown or variable, the step is treated as a **goal**
and handed to the ReAct loop, which perceives the live page and reasons its
way through.

---

## 4 · The ReAct loop — what makes it agentic

```
   ┌─────────────────────────────────────────────┐
   │  OBSERVE   wait for the page to settle,       │
   │            screenshot + extract elements      │
   │     │                                         │
   │     ▼                                         │
   │  REASON    send Gemini the goal, the screen,  │
   │            memory hints, and the FULL         │
   │            trajectory so far                  │
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
  (thought, action, observation), so it never repeats a failed approach.
  Past *screenshots* are referenced, not re-embedded, to bound token cost.
- **Idempotency first** — at iteration 1 the agent checks whether the step's
  `success_condition` is *already* satisfied and emits `done` immediately if so.
- **Wait-for-stable before every Observe** — most "screenshotted mid-render"
  flake is eliminated here, not in the reasoning.
- **A rich action vocabulary** — `click`, `fill_field_by_label`, `click_text`,
  `navigate`, `scroll`, `dismiss_obstruction`, `open_app` (enter a connected
  app logged in — see §6), `captcha_detected` (pause for a human, never solve),
  `done`, `give_up`.
- **Anti-loop guards** — a repeated no-progress action is mechanically blocked,
  so a confused agent physically cannot spin on the same failing move.
- **Hard budgets** — per-step max iterations and wall-time.
- **Every iteration is traced** — `{thought, action, observation,
  screenshot_ref, latency, tokens}` is recorded and persisted. This is both
  the debugging tool and the evidence that the agent genuinely reasons.

---

## 5 · Memory — the system gets better with every run

Memory is a backend subsystem the runner calls at two points. It is
best-effort: a memory failure never breaks a run.

| Store | Answers | Written when |
|---|---|---|
| **Procedural** | "How do I do this whole task?" — a distilled recipe | On success; reinforced across runs; down-weighted when it rots |
| **Episodic** | "Have I hit THIS situation before, and what happened?" | On step failures, obstructions, give-ups, human interventions |
| **Lessons** | Reflexion-style takeaways from a run | After each run |

- **`prime_steps()`** runs *before* a run: it looks up procedural + episodic
  memory keyed to the task and each step, and attaches per-step hint text the
  sandbox injects into that step's prompt.
- **`reflect_after_run()`** runs *after*: it distills the run into procedural
  memory, captures episodes, and emits lessons.
- **Trust gate** — a procedure is only surfaced once it has a real track record
  (multiple successes, high success rate), so a single lucky run isn't trusted
  blindly.
- **Retrieval** is a normalized, keyword-based **task signature** (deliberately
  non-embedding for v1: deterministic, debuggable, free). An embedding layer
  can sit on top later without changing the stored schema.

Stores are SQL-backed and persist across restarts.

---

## 6 · The security model

### 6.1 — Zero-trust sandbox
The sandbox is the only thing touching untrusted web pages, so it's the one
thing that holds **no long-lived secrets**. It gets a single `RUN_TOKEN`
(random, hashed-on-the-Run, useless outside its own run) and nothing else.

### 6.2 — The LLM proxy
The sandbox never holds the LLM API key. Its `llm_client` is a thin HTTP
client that calls the backend's `/sandbox/llm/generate`, authenticating with
the `RUN_TOKEN`. The backend makes the real Gemini call with *its* key.

```
sandbox.llm_client ──(RUN_TOKEN)──▶ backend /sandbox/llm ──(API key)──▶ Gemini
```

A `printenv` inside the container reveals no API key. The key lives in exactly
one place.

### 6.3 — The credential vault
OAuth tokens and API keys are **Fernet-encrypted** before they touch the
database. The encryption key lives only in `VAULT_ENCRYPTION_KEY` (env var,
never in the DB). Non-secret metadata (`instance_url`, `scope`) is stored in
the clear so the system can show connection status without decrypting.

### 6.4 — Per-run token validation
Every sandbox → backend call (`/mcp`, `/sandbox/llm`, `/sandbox/frontdoor`)
carries the `RUN_TOKEN` as a bearer credential. The backend hashes it and
compares to the hash stored on the Run. A compromised sandbox can act only for
its own run — it cannot swap a run_id to reach another user's data.

---

## 7 · Connecting to Salesforce — OAuth + `singleaccess`

This is the part that lets the agent operate a *logged-in* Salesforce UI
without ever holding the Salesforce token.

### 7.1 — One-time connection (OAuth)

```
You → /oauth/salesforce/connect → Salesforce login → click Allow
   → Salesforce redirects the code to YOUR /oauth/salesforce/callback
   → backend exchanges code for tokens
   → tokens encrypted into the vault
```

### 7.2 — Per-run login (`singleaccess`)

When the agent decides it needs Salesforce, it emits the **`open_app`** action.
The system turns that into a logged-in session — lazily, on demand, without the
sandbox ever seeing the token:

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

The raw token exists only in the vault and in the backend's memory during the
`singleaccess` call. The browser only ever follows a **single-use** URL —
worthless after one use.

**Auth is the system's job; navigation is the agent's job.**

---

## 8 · Reliability

- **Honors `on_failure`** — a failed step is routed by its declared policy:
  `abort` stops the run, `pause` halts for human review, `skip` proceeds to the
  next step, `retry` re-attempts.
- **Per-step idempotency** — every state-changing step carries a
  `success_condition` checked *before* acting (skip if done) and *after*
  (verify). This makes re-runs and partial-completion recovery automatic.
- **Drain-the-queue loops** — a `loop` over `__drain__` processes a filtered
  list until empty, with the first body step doubling as the empty-list
  sentinel.
- **Transient-error retries** — the LLM proxy retries rate/overload and the
  occasional bad-image blip, with backoff.
- **Quota circuit-breaker** — daily quota exhaustion fails fast and aborts the
  whole run rather than marching every remaining step into the same wall.

---

## 9 · Directory map

```
backend/app/
  main.py            FastAPI app; runs migrations on startup
  config.py          single typed Settings object (12-factor, env-driven)
  api/               thin HTTP routers (plans, automations, runs, oauth,
                       credentials, mcp, sandbox_llm, sandbox_frontdoor)
  agent/             video → plan pipeline
    video_processor.py       recording → keyframes
    audio_transcriber.py     recording → narration timeline
    keyframe_captioner.py    keyframes → vision captions
    plan_generator.py        captions → structured Plan
  core/
    guardrails/      plan/action safety checks
    budget/          per-run cost + iteration budgets
    prompts/         shared prompt fragments
    llm/             provider client + factory
  services/
    run_repo.py      the single SqlRepo (all DB access goes through it)
    vault.py         Fernet-encrypted credential storage
    oauth/           OAuth 2.0 flow + automatic token refresh
    mcp/             MCP servers wrapping external APIs as tools
    memory/          procedural · episodic · lessons stores + prime/reflect
    sandbox/         sandbox runners (local_docker, modal, fargate)
  db/                SQLAlchemy 2.0 async models + migrations
  schemas/           Pydantic models — the API contract

sandbox_agent/       (runs INSIDE the container)
  main.py            sandbox HTTP server (/run)
  executor.py        walks the Plan, dispatches each step (seq + ReAct)
  browser_mode.py    the ReAct loop + sequence sub-action primitives
  computer_mode.py   xdotool desktop fallback
  grounding.py       screenshot annotation + DOM element extraction
  llm_client.py      → backend LLM proxy (no key held)
  mcp_client.py      → backend MCP endpoint

frontend/            Next.js dashboard (upload · plan review · live run · history)
sandbox/             Docker image definition (Chromium + noVNC + agent server)
```

---

## 10 · Design principles

- **The Plan is the contract.** Components couple to the Plan, not to each
  other — change one without rippling the rest.
- **Deterministic where you can, agentic where you must.** Known UI patterns
  become `sequence` steps; only genuinely variable steps pay for an LLM.
- **No layer reaches around another.** The API never touches SQL directly; the
  sandbox never touches a credential.
- **Config, not code, changes between environments.** Dev → prod is setting
  environment variables, never editing source.
- **The system learns.** Every run feeds memory; memory primes the next run.

---

## 11 · Known limitations (honest list)

- **Single-user by default.** Runs execute as one default user; full
  multi-user data scoping is still being hardened.
- **Keyword memory retrieval.** Task signatures are keyword-based; near-duplicate
  tasks with reworded steps can fragment into separate procedures until an
  embedding layer is added.
- **LLM latency dominates run time.** The ReAct loop is the cost center;
  moving more steps to `sequence` and trimming per-observe waits is the active
  optimization front.
- **CAPTCHA is an explicit non-goal.** The agent pauses and hands off; it never
  attempts to solve one.
