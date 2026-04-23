# Spike 001: Playwright + Gemini Computer Use

First week spike. Goal: prove the execution substrate works end-to-end on a
simple public site before committing to the full architecture.

## What it does

Runs a Gemini 3 Computer Use agent loop against a local Playwright browser.
Target task: log into `https://the-internet.herokuapp.com/login` with the
provided credentials, then log out and verify the login page reappears.

## Why this task

- Multi-step (type username, type password, click submit, click logout)
- Stable test site that will not change
- Clear success state (we check URL + page content)
- Tests typing, clicking, and state-change detection

## What we learn from this spike

1. Does Gemini Computer Use actually complete a basic form task reliably?
2. How many turns does it take? (proxy for token cost)
3. What does the accessibility tree look like on a real page, and is it
   useful as supplementary context?
4. How often does the safety system require confirmation?
5. What does our usage tracking and budget-enforcement layer look like in
   practice?

Once this works, swapping target to Salesforce is a prompt change.

## Design choices baked in

- `LLMClient` abstraction (swap to Anthropic with one line later)
- Per-run budget caps: 25 model calls, $0.50, 5 min wall-clock
- Every turn traced to `traces/<run_id>/` for debugging
- Accessibility tree extracted and injected as supplementary context
- Built on Google's official reference pattern (denormalized coords, safety
  acknowledgement handling, full action set)

## Run it

```bash
cd spikes/001_playwright_gemini_cu

# Install
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# edit .env and add GEMINI_API_KEY

# Run
python spike.py
```

## Files

- `spike.py` - the agent loop (entry point)
- `llm_client.py` - Gemini wrapper with usage tracking + provider abstraction
- `grounding.py` - accessibility tree extraction and compacting
- `budget.py` - per-run kill switches
- `actions.py` - translates Gemini Computer Use function calls to Playwright
- `traces/` - per-run trace directories (screenshots + JSON)