# Existing development instructions

Preserved from the supplied README snapshot to avoid losing setup guidance during the documentation rewrite. These instructions have not been rerun in this review environment. Maintainers must verify authentication headers, example file paths, model configuration and the separate Automation creation step against the current implementation before publication. Historical provider quota statements below are unverified. Commands assume the repository root or backend directory as indicated.

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

> 💡 The ReAct loop is the run's cost center. For faster/cheaper runs, point
> `LLM_MODEL` at a Flash-class model — the loop is mostly mechanical clicks and
> fills that don't need a heavyweight reasoning model.

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
