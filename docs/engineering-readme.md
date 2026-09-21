# 🤖 AI Work Automation Agent

_Engineering reference: setup, current capabilities, and operational detail. For the project overview and intent, see the [root README](../README.md)._

> Record a task once. Review the plan the agent writes. Run it in an isolated sandbox and watch it work.

**Status: working proof of concept.** It turns a screen recording into an executable plan, runs that plan in a throwaway Docker container, and drives a real browser with a Reason → Act → Observe loop. It is not a finished product: read [What works and what doesn't](#-what-works-and-what-doesnt) before you rely on any part of it.

The focus is **Salesforce data hygiene** (updating cases, contacts and tasks). The architecture is not Salesforce-specific, but the prompts and the deterministic UI primitives currently are — treat "works on any web app" as untested.

---

## ✨ What makes it different

| | |
|---|---|
| 🎥 **Learns by watching** | Record yourself once. No scripting, no selectors, no brittle macros. |
| 🧠 **Genuinely agentic** | A ReAct loop reasons over live screenshots and adapts — it doesn't replay fixed clicks. |
| ⚡ **Deterministic where it counts** | Well-understood UI patterns run as `sequence` steps — no LLM in the loop, ~5–7× faster and rock-solid. |
| 📈 **Learns across runs** | Procedural + episodic memory primes each run with what worked (and what didn't) before. Improvement is not guaranteed and is not yet measured. |
| 🔒 **No long-lived secrets in the sandbox** | The container never holds an API key or an OAuth token. It does drive a **logged-in browser session**, so it is not powerless — see [Security model](#-security-model-honestly). |
| 👁️ **Watchable & auditable** | Watch runs live; every run keeps a full step-by-step reasoning trace. |
| 🔌 **Connected apps, on demand** | The agent decides *when* it needs Salesforce and logs in itself — via a one-time token, never a password. |

---

## 🎯 How it works, in five steps

```
   1. RECORD              2. PLAN                3. RUN
   ┌─────────┐          ┌─────────┐           ┌──────────────┐
   │ screen  │  ──────▶ │ executable │ ──────▶ │ isolated      │
   │ recording│         │ plan       │         │ sandbox       │
   └─────────┘          └─────────┘           └──────────────┘
                                                      │
   5. INSPECT            4. WATCH                      ▼
   ┌─────────────┐      ┌─────────────┐         ┌──────────────┐
   │ full reasoning│ ◀── │ live browser │ ◀────── │ ReAct agent   │
   │ trace + cost  │     │ view (noVNC) │         │ drives the UI │
   └─────────────┘      └─────────────┘         └──────────────┘
```

1. **Record** a screen capture of the task once.
2. The platform turns it into a structured **Plan** (FFmpeg keyframes → vision-LLM captions → plan synthesis).
3. You trigger it; the backend **primes the run from memory**, then a **fresh Docker sandbox** spawns and executes the plan autonomously.
4. **Watch it work live** in your browser.
5. **Inspect** the full Reason → Act → Observe trace afterward — every thought, action, and observation. The backend **reflects on the run** so the next one starts smarter.

---

## 🏛️ Architecture in one picture

Two processes, one hard security boundary:

```
   You / CLI
      │
      ▼
 ┌────────────────────────────────────────────┐
 │  🧠 BACKEND  — holds ALL secrets            │
 │     • API layer        • credential vault   │
 │     • video→plan       • OAuth + frontdoor  │
 │     • LLM proxy        • sandbox runner     │
 │     • database (SQLite / Postgres)          │
 └────────────────────────────────────────────┘
      │  spawns + sends Plan      ▲  LLM & tool calls
      ▼  (scoped per-run token)   │  (no secrets travel down)
 ┌────────────────────────────────────────────┐
 │  🦾 SANDBOX — Docker, one per run           │
 │     • executor → ReAct loop → Chromium      │
 │     • holds ONLY a per-run token            │
 └────────────────────────────────────────────┘
      │
      ▼
   🌐 Gemini   ☁️ Salesforce
```

**The backend is the brain and the vault. The sandbox is the hands.** The hands never hold the keys.

### 🔐 Security model, honestly

**What the boundary does give you.** No long-lived secret ever enters the
container:

- 🔑 **The LLM API key never enters the sandbox.** Model calls are proxied through the backend, which holds the key.
- 🎫 **The Salesforce OAuth token never enters the sandbox.** The agent logs in via Salesforce's `singleaccess` endpoint, which mints a one-time URL on the backend; the token stays in the encrypted vault.
- 🪪 **Per-run tokens are scoped and short-lived.** Only a SHA-256 hash is stored, the token is refused once the run ends, and it can only call the MCP tools the approved plan declared.

**What it does not give you.** The browser inside the sandbox holds a
**logged-in Salesforce session** for the length of the run. That is the whole
point of the frontdoor, and it means:

- code running in that container can act in Salesforce **as you**, within that session, until the run ends and the container is destroyed;
- the blast radius is one run and whatever that session can reach, not your refresh token and not your other integrations;
- the sandbox control port and the live view are therefore published on **loopback only** by default. Do not expose them without putting authentication in front (`SANDBOX_BIND_HOST`, `SANDBOX_VNC_PASSWORD`).

Treat the sandbox as "cannot steal your credentials", not as "cannot do damage".
Run it against a Salesforce **Developer Edition org**, not production.

Full design: see [`Architecture.md`](./Architecture.md).

---

## 🔍 What works and what doesn't

| Area | State |
|---|---|
| Recording → plan → review → approve → run | **Works.** Exercised end to end over HTTP, including in mock mode with no API key. |
| ReAct browser execution in a sandbox | **Works** against Salesforce Lightning for the demonstrated flows. |
| Deterministic `sequence` steps | **Works** for inline record-page field edits. |
| Memory (procedural, episodic, lessons) | **Works**, SQL-backed. Its effect on success rate is **not measured**. |
| Pause and resume | **Implemented, lightly tested.** A resumed run starts a fresh browser, replays login/navigation, and skips steps already completed. Not yet validated against a real multi-step Salesforce pause. |
| `notify` steps | **Not implemented.** There is no email or Slack delivery; the step reports `skipped` and sends nothing. |
| Scheduling / triggers | **Not implemented.** The `Schedule` field exists on the model; nothing reads it. There is no scheduler. |
| Cloud sandbox runners (Modal, Fargate) | **Stubs.** They implement the interface and raise `NotImplementedError`. `local_docker` is the only working runner. |
| Multi-user | **Implemented and tested** for data scoping. Execution is still a single in-process background task per run. |
| Durable run queue | **No.** Runs do not survive a backend restart; on boot, abandoned runs are marked failed. |
| Idempotency | **Per step, where a `success_condition` exists and the checker understands it.** Not universal — see below. |
| Evaluation harness | **None.** Success is currently judged by the agent itself. |

### About idempotency

Steps that carry a `success_condition` are checked before acting and verified
after, so a re-run of those steps skips work already done. This is **not** a
global guarantee:

- steps without a `success_condition` will repeat their action on a re-run;
- the deterministic checker understands two condition phrasings and returns
  "unknown" for anything else, in which case the step runs rather than skipping;
- for agentic steps the check is made by the model reading the screen, so it is
  a judgement, not a transaction.

Re-running a plan is therefore usually safe but **not guaranteed to be free of
duplicates**. Verify on your own data before trusting it unattended.

---

## 🚀 Quick start

### Prerequisites

- **Python 3.12**
- **Docker Desktop** (running)
- **ffmpeg** — `brew install ffmpeg`
- A **Google Gemini API key** ([aistudio.google.com](https://aistudio.google.com)) — *billing recommended; the free tier caps at 20 calls/day per project*
- *(optional)* a **Salesforce org** you control — a free [Developer Edition](https://developer.salesforce.com/signup) works perfectly

### 1 · Install

```bash
git clone <your-repo-url> salesforce-agent
cd salesforce-agent/backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2 · Configure `backend/.env`

```bash
cp .env.example .env
```

```ini
GEMINI_API_KEY=<your key>
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.1-pro-preview     # the backend proxies all model calls; the sandbox never sees the key

# generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
VAULT_ENCRYPTION_KEY=<generated key>

PUBLIC_BACKEND_BASE_URL=http://localhost:8001
```

> 💡 **Two model jobs, independently configurable.** The video → plan pipeline
> (`LLM_PROVIDER` / `LLM_MODEL`) makes a few long vision calls once per
> recording. The ReAct loop inside the sandbox (`SANDBOX_LLM_PROVIDER` /
> `SANDBOX_LLM_MODEL`) makes many small strict-JSON calls on every run and is
> the cost center. Leave the `SANDBOX_*` pair unset to use one model for both,
> or split them, e.g. Gemini for captioning and Claude for the loop:
>
> ```ini
> LLM_PROVIDER=gemini
> LLM_MODEL=gemini-2.5-flash
> GEMINI_API_KEY=...
> SANDBOX_LLM_PROVIDER=anthropic
> SANDBOX_LLM_MODEL=claude-opus-5
> ANTHROPIC_API_KEY=...
> ```
>
> The sandbox holds no key either way — every model call is proxied through the
> backend. `GET /health` reports which model serves each job.
>
> **Claude for everything**, including captioning and plan synthesis:
>
> ```ini
> LLM_PROVIDER=anthropic
> LLM_MODEL=claude-sonnet-5
> ANTHROPIC_API_KEY=...
> # optional per-stage split: captioning is token-heavy but easy,
> # plan synthesis is one demanding call
> CAPTION_LLM_MODEL=claude-haiku-4-5
> PLAN_LLM_MODEL=claude-opus-5
> ```
>
> **Working against prepaid credits?** Set `DEFAULT_MAX_USD_PER_RUN` to
> something real (e.g. `2.00`). The shipped default is effectively unlimited,
> and this ceiling is what stops one confused run from draining the balance.

> ⚠️ **Don't also `export GEMINI_API_KEY` in your shell.** A shell variable overrides `.env` and causes "wrong key" confusion. Keep it only in `.env`.

### 3 · Build the sandbox image

```bash
cd ..                       # repo root
docker build -t agent-sandbox:latest -f sandbox/Dockerfile .
docker image inspect agent-sandbox:latest >/dev/null 2>&1 && echo "✅ FOUND" || echo "❌ MISSING"
```

### 4 · Run the backend

```bash
cd backend
python -m app.main
# → "Backend up." on http://localhost:8001
curl -s http://localhost:8001/health | python -m json.tool   # vault_configured: true
```

### 5 · Turn a recording into a plan

```bash
# upload → returns {"video_id": "...", "status": "uploaded"}
curl -s -F "file=@recording.mp4" http://localhost:8001/videos | python -m json.tool

# poll until status is "completed" and plan_id is set
curl -s http://localhost:8001/videos/<video_id> | python -m json.tool

# review it, then approve
curl -s http://localhost:8001/plans/<plan_id> | python -m json.tool
curl -s -X POST http://localhost:8001/plans/<plan_id>/approve
```

A plan lands as `pending_approval` — nothing runs until a human approves it.
To reshape it, describe the change in plain language:
`POST /plans/<plan_id>/correct  {"feedback": "do this for every new case, not just 00001378"}`.

### 6 · Run a demo (no credentials needed)

```bash
python -m scripts.run_plan_e2e .local_storage/plans/plan_arxiv_demo.json --watch
```

Open the printed `watch:` URL to see the agent live. When it finishes:

```bash
curl -s http://localhost:8001/runs/<run_id> | python -m json.tool
#   → step_executions[].trace shows the agent's reasoning, step by step
```

---

## 🔗 Connect Salesforce *(optional, one-time)*

A one-time account connection — afterward the platform keeps the token fresh automatically.

1. **Create a Connected App** in your Salesforce org (Setup → App Manager → New Connected App):
   - Enable OAuth Settings
   - Callback URL: `http://localhost:8001/oauth/salesforce/callback`
   - Scopes: **Full access (full)**, **Manage user data via APIs (api)**, **Perform requests at any time (refresh_token, offline_access)**
   - Save, then **wait ~10 min** for it to propagate
2. Copy the **Consumer Key + Secret** into `.env` (`SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`), keep `SALESFORCE_AUTH_URL=https://login.salesforce.com`, restart the backend.
3. **Connect:** open `http://localhost:8001/oauth/salesforce/connect` in a browser → log in → Allow → green ✅ page.
4. **Verify:** `curl -s http://localhost:8001/oauth/providers` → salesforce `"connected": true`.

The token is now encrypted in the vault. From here on, any plan that needs Salesforce just works — the agent logs itself in on demand via a one-time URL, never touching the token.

---

## 🗂️ Project layout

```
salesforce-agent/
├── backend/
│   └── app/
│       ├── main.py          # FastAPI app; migrations on startup
│       ├── config.py        # all settings, from environment
│       ├── api/             # HTTP endpoints (plans, automations, runs,
│       │                    #   oauth, mcp, sandbox_llm, sandbox_frontdoor)
│       ├── agent/           # video → plan pipeline
│       ├── core/            # guardrails, budgets, prompts, LLM factory
│       ├── services/        # vault, oauth, mcp, memory, sandbox runner
│       ├── db/              # SQLAlchemy models + migrations
│       └── schemas/         # Pydantic contracts
├── frontend/                # Next.js dashboard (upload · plan · run · history)
├── sandbox/                 # Docker image definition
└── sandbox_agent/           # code that runs INSIDE the container
    ├── executor.py          # walks the Plan; sequence + ReAct dispatch
    ├── browser_mode.py      # ReAct loop + sequence sub-action primitives
    ├── grounding.py         # screenshot annotation + DOM extraction
    ├── llm_client.py        # → backend LLM proxy (no key here)
    └── mcp_client.py        # → backend MCP endpoint
```

> **Plan steps come in two flavors:** deterministic `sequence` steps (ordered
> sub-actions like `click_pencil_icon` → `fill_field` → `click_dropdown_result`,
> run with no LLM) and agentic `ui_action`/`extract` steps (handed to the ReAct
> loop). Prefer `sequence` for known UI patterns — it's faster and never wanders.

---

## 🛠️ Everyday commands

```bash
# start backend (shell where GEMINI_API_KEY is NOT exported)
python -m app.main

# free port 8001 if a stale backend holds it
lsof -ti:8001 | xargs kill -9

# rebuild sandbox image — REQUIRED after editing anything in sandbox_agent/
docker build --no-cache -t agent-sandbox:latest -f sandbox/Dockerfile .

# run a plan with live view
python -m scripts.run_plan_e2e .local_storage/plans/<plan>.json --watch

# inspect a run + its reasoning trace
curl -s http://localhost:8001/runs/<run_id> | python -m json.tool
```

---

## 🩹 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `address already in use` on startup | Stale backend holds the port → `lsof -ti:8001 \| xargs kill -9`, restart. |
| Wrong / old Gemini key used | `GEMINI_API_KEY` exported in your shell overrides `.env` → remove from `~/.zshrc`, fresh terminal (`echo $GEMINI_API_KEY` blank), restart. |
| `Sandbox image not found` | Image misnamed or backend started before build → `docker image inspect agent-sandbox:latest`; re-tag/rebuild, restart. |
| `sandbox_agent/` edits do nothing | You must **rebuild the image**. Backend = restart; sandbox = rebuild. |
| "Salesforce not connected" despite being connected | Source of truth is the **SQLite DB**, not the `.local_storage/*.json` files. Check via the API, not the files. |
| Runs abort with "quota exhausted" | Free Gemini tier = 20 calls/day **per project**. A multi-step run exhausts it. Enable billing. |

---

## 📍 Status

**✅ Built & verified**
- Video → plan pipeline (keyframes · narration · captions · plan synthesis)
- Executor with two step engines: deterministic `sequence` steps + agentic ReAct loop
- Full reasoning trace + cost tracking per run
- Per-step idempotency via `success_condition`; drain-the-queue loops
- Learning loop — procedural + episodic memory primes each run; reflection after
- Isolated Docker sandbox with live (noVNC) view
- LLM proxy — sandbox holds no API key
- Per-run token auth for every sandbox → backend call
- Encrypted credential vault + OAuth (Salesforce)
- `singleaccess` FrontDoor — one-time login URL, token never leaves the backend
- **Agent-initiated `open_app`** — logs into a connected app lazily, mid-task
- Quota circuit-breaker + per-run call/dollar budget — fails fast & clean
- Plan guardrails — an invalid or unapproved plan can never reach a sandbox
- Approval is server-controlled: a client cannot mark its own plan approved, and
  a run executes the exact approved snapshot, not a later edit
- Executor honors per-step `on_failure` (`abort` / `pause` / `skip` / `retry`)
- Resumable pauses — answer a stuck run and it continues from that step
- Per-user scoping on every route; credentials and runs are never shared
- `POST /videos` — upload a recording and poll it into a Plan
- End-to-end Salesforce run verified (contact update · status escalation · task creation)
- Next.js dashboard (upload · plan review · live run · history)

**🗺️ Roadmap**
- Durable run queue — execution is in-process today, so runs don't survive a restart
- Cloud sandbox runners (Modal / Fargate are interface stubs today)
- Embedding-based memory retrieval on top of keyword signatures
- Run-speed pass — move more steps to `sequence`, trim per-observe waits
- Scheduling & triggers