# Architecture Diagram — Generation Prompt

Paste the prompt below into a capable multimodal LLM (or an image/design tool)
to generate a **polished, visually-rich, modular architecture diagram** of the
AI Work Automation Agent — the kind you'd put in a README hero, a pitch deck,
or a docs landing page. It is **not** a plain flowchart: it asks for a designed
infographic with zones, icons, color, and numbered step badges.

It is self-contained — every component, boundary, and step is included, so the
model doesn't need the codebase.

The prompt targets a **self-contained SVG** (crisp at any size, embeds anywhere,
theme-friendly). Alternatives (image-generation, Excalidraw) are noted at the
end.

---

## The prompt

> You are a senior visual/infographic designer. Create a **beautiful, modern,
> high-level architecture diagram** for a product called the **AI Work
> Automation Agent** — a platform that turns a single screen recording into a
> repeatable browser automation run by an autonomous agent inside a secure
> sandbox.
>
> **Deliverable:** one self-contained **SVG** (all styles inline, no external
> assets, no external fonts — use a system sans stack). Landscape, ~1600×1000,
> `viewBox` set so it scales crisply. It should look like a designed product
> infographic, **not** a boxes-and-arrows flowchart.
>
> ### Visual design language
> - **Two large zones** side by side, each a rounded "glass card" panel with a
>   soft shadow and a header strip:
>   - 🧠 **BACKEND — brain + vault** (long-running, holds ALL secrets)
>   - 🦾 **SANDBOX — hands, one per run** (fresh Docker container, holds NO
>     secrets, only a scoped per-run token)
> - Between the two zones, draw a **vertical "trust boundary"** as a stylized
>   dashed seam with a small shield/lock motif and the caption:
>   *"the component that touches the web never holds the secrets."* Make this
>   boundary a clear focal element.
> - Distinct color families: cool indigo/violet for BACKEND, teal/green for
>   SANDBOX, warm amber for EXTERNAL systems. Muted, professional, good
>   contrast; subtle gradients allowed. Include a small legend.
> - Represent each module as a **labeled "chip" or mini-card with a simple
>   line-icon** (draw icons as inline SVG paths — database, key/vault, gear,
>   eye, browser window, brain, robot arm, cloud). No emoji dependence for the
>   icons themselves; emoji only in headers if convenient.
> - Use clean typographic hierarchy: zone titles bold, module labels medium,
>   captions small and muted.
>
> ### Modules to place
> Inside **BACKEND**: `api` (HTTP layer), `agent` (video→plan pipeline),
> `services` (repo · vault · oauth · mcp · memory · sandbox-runner), `core`
> (guardrails · budget · prompts), `db` (SQLite dev / Postgres prod). Give the
> **vault** and **memory** modules extra visual weight (they're differentiators).
> Inside **SANDBOX**: `executor` feeding two clearly contrasted engines —
> **`sequence` steps (deterministic, no LLM)** and **`ReAct loop` (LLM-driven:
> Observe → Reason → Act)** — both driving a **Chromium** browser icon; plus
> `llm_client` and `mcp_client` tagged "no secrets."
> Outside both zones, as **external systems** (amber): **Gemini** (vision +
> reasoning LLM) and **Salesforce** (target app).
>
> ### The stepwise flow — the visual spine
> Overlay the lifecycle as **numbered step badges (circled 1–9)** connected by
> smooth, directional ribbons/arrows. Each badge sits at the relevant module and
> carries a short label. Render the numbers prominently so a reader can "walk
> the story" 1→9:
> 1. **Record** — user uploads a screen recording to `api`.
> 2. **Build Plan** — `agent`: ffmpeg keyframes → vision captions (via Gemini) →
>    plan synthesis → Plan saved to `db`.
> 3. **Trigger + Prime** — run triggered; `memory` primes per-step hints from
>    past runs; a per-run token is minted (only its hash is stored).
> 4. **Spawn + send Plan** — sandbox-runner spawns the SANDBOX, injecting Plan +
>    token + memory hints (no secrets travel down — annotate this arrow).
> 5. **Execute** — `executor` runs deterministic `sequence` steps directly and
>    sends variable steps to the `ReAct loop`.
> 6. **LLM proxy** — `llm_client` calls back to `api` with the token; the backend
>    makes the real Gemini call with ITS key (label: "API key never enters the
>    sandbox").
> 7. **open_app / singleaccess** — backend reads the vault token, mints a
>    ONE-TIME login URL, and redirects the sandbox browser into a logged-in
>    Salesforce session (label: "token stays server-side").
> 8. **Return trace** — sandbox returns each step's result + full reasoning
>    trace; backend persists to `db` and destroys the container.
> 9. **Reflect** — `memory` distills the run (procedural + episodic + lessons)
>    and **loops back to step 3 of the next run**. Draw this as a bold,
>    highlighted **"learning loop"** feedback ribbon curving back to priming.
>
> ### Composition
> Lead the eye left→right for steps 1–8, with step 9 as a graceful return curve
> that visually closes the loop. Balance whitespace; don't crowd. The finished
> piece should read as *"record → plan → prime → run → learn, forever,"* with
> the security boundary as the hero motif.
>
> Output ONLY the SVG code.

---

### To target a different medium

- **Image-generation model (Midjourney / DALL·E / etc.):** keep the "Visual
  design language," "Modules," and "stepwise flow" descriptions, but replace the
  deliverable line with: *"Describe a single wide infographic image: two glass-
  panel zones split by a glowing trust boundary, line-icon module chips,
  numbered step badges 1–9 connected by flowing ribbons, indigo/teal/amber
  palette, clean and modern, poster quality."* (Image models can't place text
  reliably — expect to add labels afterward.)
- **Excalidraw / draw.io / Figma:** replace the deliverable line with:
  *"Output a structured node + edge spec — each node: id, label, group
  (backend/sandbox/external), icon, x/y; each edge: from, to, step-number,
  label — ready to import and then style by hand."*

---

### Reference: the flow in one line

`Record → Build Plan → Trigger + Prime → Spawn → Execute (sequence | ReAct) →
LLM proxy → open_app/singleaccess → Return trace → Reflect ↺`

See [`../Architecture.md`](../Architecture.md) for the authoritative,
prose-level description this prompt is distilled from.
