# 🤖 AI Work Automation Agent

> Watch a task once. The agent does it forever — autonomously, in a secure sandbox, reasoning its way through any web UI.

An autonomous agent platform that turns a **single screen recording** into a repeatable automation. It generates an executable plan, runs it in an isolated cloud sandbox, and uses a **Reason → Act → Observe loop** to perceive a live browser, think through each step, and adapt in real time — the way a person would.

The first focus is **Salesforce data hygiene** (creating and updating leads/contacts), but nothing about the architecture is Salesforce-specific. It's a general web-task agent.

---

## ✨ What makes it different

| | |
|---|---|
| 🎥 **Learns by watching** | Record yourself once. No scripting, no selectors, no brittle macros. |
| 🧠 **Genuinely agentic** | A ReAct loop reasons over live screenshots and adapts — it doesn't replay fixed clicks. |
| 🔒 **Zero-trust by design** | The sandbox that touches the web **never holds a credential or API key.** Ever. |
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
3. You trigger it; a **fresh Docker sandbox** spawns and executes the plan autonomously.
4. **Watch it work live** in your browser.
5. **Inspect** the full Reason → Act → Observe trace afterward — every thought, action, and observation.

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

Two security properties this guarantees:

- 🔑 **The LLM API key never enters the sandbox.** All model calls are proxied through the backend.
- 🎫 **The Salesforce token never enters the sandbox.** The agent logs in via Salesforce's `singleaccess` endpoint, which mints a **one-time login URL** on the backend — the real token stays locked in the vault.

Full design: see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

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
LLM_MODEL=gemini-2.5-flash

# generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
VAULT_ENCRYPTION_KEY=<generated key>

PUBLIC_BACKEND_BASE_URL=http://localhost:8001
```

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

### 5 · Run a demo (no credentials needed)

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
│       ├── services/        # vault, oauth, mcp, frontdoor, sandbox runner
│       ├── db/              # SQLAlchemy models + Alembic migrations
│       └── schemas/         # Pydantic contracts
├── sandbox/                 # Docker image definition
└── sandbox_agent/           # code that runs INSIDE the container
    ├── executor.py          # walks the Plan, dispatches each step
    ├── browser_mode.py      # the ReAct loop (Reason-Act-Observe)
    ├── llm_client.py        # → backend LLM proxy (no key here)
    └── mcp_client.py        # → backend MCP endpoint
```

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
- Video → plan pipeline
- ReAct executor loop with full reasoning trace + cost tracking
- Isolated Docker sandbox with live (noVNC) view
- LLM proxy — sandbox holds no API key
- Per-run token auth for every sandbox → backend call
- Encrypted credential vault + OAuth (Salesforce)
- `singleaccess` FrontDoor — one-time login URL, token never leaves the backend
- **Agent-initiated `open_app`** — the agent decides on its own when it needs a connected app and logs in lazily, mid-task
- Quota circuit-breaker — fails fast & clean instead of hanging
- Executor honors per-step `on_failure` (abort / pause / continue)

**🚧 In progress**
- Final end-to-end Salesforce-UI run (mechanism verified; gated on LLM quota)

**🗺️ Roadmap**
- Multi-user + authentication (currently single default user)
- Web dashboard (upload · plan review · live run · history)
- Scheduling & triggers