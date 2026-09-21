# Architecture

_Detailed internals: execution model, memory, security mechanics, and directory map. For the conceptual overview, see [Architecture.md](../Architecture.md)._

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

`POST /videos` accepts the upload and runs the pipeline in the background;
`GET /videos/{id}` polls it until `plan_id` is set. (The same stages are
available offline via `python -m scripts.process_video`.)

```
screen recording → [video_processor]     → keyframes (ffmpeg)
                 → [audio_transcriber]    → narration timeline (optional)
                 → [keyframe_captioner]   → per-frame descriptions (Gemini vision)
                 → [plan_generator]       → structured Plan → saved to DB
```

Captions are persisted to `captions/<video_id>.json` so a later correction
(`POST /plans/{id}/correct`) can regenerate the plan against the original
recording rather than against the previous plan alone.

The **Plan is the master contract** of the whole system. Everything upstream
produces it; everything downstream consumes it. A Plan is an ordered list of
typed **Steps**, each with a `success_condition` (for state-changing steps)
and an `on_failure` policy (`pause` · `skip` · `abort` · `retry`).

### 2.2 — Plan becomes a Run

```
POST /automations/{id}/run
   → guardrails: plan must be APPROVED and pass validation (§8)
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
  non-embedding for v1: deterministic, debuggable, free). An exact signature
  match is tried first; failing that, the closest stored signature by token
  overlap (Jaccard ≥ 0.6, plural- and quantifier-insensitive) is used — so a
  reworded goal finds the procedure it already learned instead of relearning
  it. An embedding layer can sit on top later without changing the schema.

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
the `RUN_TOKEN`. The backend makes the real provider call with *its* key.

The backend also chooses the provider and model. The class inside the sandbox
is named `GeminiClient` for historical reasons but talks only to the proxy, so
the ReAct loop can run on Claude while the video → plan pipeline runs on
Gemini without changing a line of sandbox code. See
`SANDBOX_LLM_PROVIDER` / `SANDBOX_LLM_MODEL`.

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
compares (constant-time) to the hash stored on the Run. A compromised sandbox
can act only for its own run — it cannot swap a run_id to reach another user's
data.

There is **no anonymous path**: a call with no run_id, an unknown run, or a
bad token is rejected with 401. These three endpoints hand out a user's
integration credentials, the backend's LLM key, and a logged-in Salesforce
session respectively, so none of them may ever fall back to a default user.

### 6.5 — Per-user scoping
Plans, automations, runs, credentials and OAuth connections are all keyed to
the authenticated user. Reads filter by owner and return 404 (not 403) for
another user's object, so the API never confirms that someone else's id
exists. Enforcement lives at one chokepoint (`services/scoping.py`) rather
than in each route.

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
  next step, and `retry` re-attempts the step once before falling back to
  `pause`. An unrecognized value is treated as `pause` (the safe default) and
  logged, rather than silently degrading to "continue".
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
- **Per-run budget** — the LLM proxy meters every model call for a run against
  a call count and dollar ceiling (`core/budget`). Exceeding either refuses
  further calls and aborts the run, and the measured spend becomes the Run's
  recorded cost.
- **Plan guardrails** — `core/guardrails` validates a plan when it is saved and
  again when it is approved: dangling loop/decision references, missing
  required details, unknown sequence sub-actions, non-http URLs and steps that
  would perform a manual login are rejected before a container is ever spawned.
- **Pause is resumable** — a paused run is answered with
  `POST /runs/{id}/resume`. The answer is recorded as a HumanIntervention (the
  highest-value episodic memory the system collects), and a fresh run restarts
  **at the paused step** carrying the earlier run's variables. This works
  because state-changing steps carry a `success_condition`: anything already
  done is observed and skipped rather than repeated.
- **No orphaned runs** — runs execute as in-process background tasks, so a
  restart would otherwise leave rows stuck in RUNNING forever. Startup closes
  them out as failed, and a concurrency cap bounds how many sandboxes run at
  once.

---

## 9 · Directory map

```
backend/app/
  main.py            FastAPI app; runs migrations on startup
  config.py          single typed Settings object (12-factor, env-driven)
  api/               thin HTTP routers (videos, plans, automations, runs,
                       auth, oauth, credentials, mcp, sandbox_llm,
                       sandbox_frontdoor)
  agent/             video → plan pipeline
    video_processor.py       recording → keyframes
    audio_transcriber.py     recording → narration timeline
    keyframe_captioner.py    keyframes → vision captions
    plan_generator.py        captions → structured Plan
  core/
    guardrails/      plan validation (structure, safety, reachability)
    budget/          per-run call/cost ceilings + model pricing
    prompts/         the plan-synthesis and captioner system prompts
    llm/             provider client + factory (gemini · anthropic · mock)
  services/
    run_repo.py      the single SqlRepo (all DB access goes through it)
    run_executor.py  spawn → execute → persist one run
    run_control.py   cancel · resume · orphan cleanup
    run_auth.py      per-run token validation (shared by sandbox endpoints)
    scoping.py       the multi-tenant chokepoint (ScopedRepo)
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

- **Multi-user, but single-process.** Data is scoped per user throughout, and
  auth is enforced on every user-facing route. Execution is still in-process
  (FastAPI background tasks) with a concurrency cap — there is no queue, so
  runs do not survive a restart and do not spread across machines. That is the
  next thing to change before real multi-tenant load.
- **Keyword memory retrieval.** Task signatures are keyword-based, with a
  token-overlap fallback for rewordings. Genuinely different phrasings of the
  same task (different vocabulary, not just different word order) can still
  fragment until an embedding layer is added.
- **LLM latency dominates run time.** The ReAct loop is the cost center;
  moving more steps to `sequence` and trimming per-observe waits is the active
  optimization front.
- **CAPTCHA is an explicit non-goal.** The agent pauses and hands off; it never
  attempts to solve one.
- **Cloud runners are stubs.** `modal.py` and `fargate.py` implement the
  interface but raise NotImplementedError; `local_docker` is the only working
  runner, and it is explicitly not for production multi-user use.
