# Architecture
## Demonstration-Guided Agentic Automation

### 1. Architectural objective

Enable a business user to demonstrate a manual process through a narrated screen recording, translate that demonstration into a reviewable execution plan, and have an agent perform the approved business actions through an application's user interface.

The selected POC does not require application-specific APIs or MCP tools to perform those business actions. Model APIs support interpretation and reasoning; backend services manage processing, authentication, plans, and execution.

### 2. System components

| Component | Responsibility |
|---|---|
| User interface | Present the available plan-review and dashboard screens. The complete upload-to-execution experience remains under development. |
| Recording interpretation pipeline | Extract narration and visual observations from the uploaded recording. |
| Plan generation and review | Translate observations into a structured plan, incorporate corrections, and record approval. |
| Run coordinator | Start the execution environment, supply the plan and run context, and collect results. |
| Agent sandbox | Operate a browser, observe the current application state, and perform plan steps. |
| Persistence and memory | Store plans and run records, and retrieve relevant hints from previous execution. |

### 3. From recording to understanding

The implementation processes the recording in stages:

1. **Extract audio:** FFmpeg extracts the audio track.
2. **Transcribe narration:** Gemini is attempted first, with optional local transcription fallbacks.
3. **Extract visual evidence:** Time sampling and scene-change detection select frames, followed by adjacent-frame deduplication.
4. **Describe the frames:** A vision-capable model generates descriptions of the selected screenshots.
5. **Associate narration:** Spoken segments near each frame's timestamp are attached to its description.
6. **Synthesize the plan:** The combined timeline is passed to the plan generator.

This is a staged interpretation pipeline rather than a single full-video model request.

Narration can explain business intent that is invisible on screen: why a record qualifies, which values are examples, and when an exception applies. Visual observations show the application state and demonstrated actions.

The current implementation can continue when transcription fails. It also supports approximate timestamp fallback. These conditions need to be surfaced during review so missing or imperfect narration is not mistaken for complete understanding.

Relevant modules: `backend/app/agent/` and `backend/app/api/videos.py`.

### 4. The plan as the execution contract

The Plan connects interpretation with execution. It contains:

- The business goal and summary.
- Execution steps and their types.
- Inferred decision rules and optional supporting explanations.
- Required credential providers.
- Step success conditions and failure policies.
- Source recording reference, version, status, and correction history.

Business intent is currently represented within this Plan. There is no separately persisted business-intent specification.

The plan generator must distinguish demonstrated examples from reusable rules. Seeing one case updated does not establish that every case should receive the same update.

A future extension could make record-selection rules, variable inputs, exclusions, evidence timestamps, and unresolved questions explicit fields.

Relevant modules: `backend/app/schemas/plan.py` and `backend/app/agent/plan_generator.py`.

### 5. Review and activation

The ordinary recording pipeline generates a plan with `pending_approval` status.

The user can review the plan and submit corrections in plain language. A correction regenerates the plan, increments its version, and returns it to pending approval. Original captions are used to anchor revisions when available.

An approved plan is wrapped in an Automation through a separate creation step. The user then requests a run.

The run endpoint checks approval status and plan validation before scheduling execution in the backend process. Stronger binding to an immutable approved version remains necessary, particularly for queued and resumed work.

Relevant modules: `backend/app/api/plans.py` and `backend/app/api/automations.py`.

### 6. Browser-based agent execution

The run coordinator starts a local Docker sandbox containing Chromium and the executor.

The executor supports two main approaches to UI work:

| Approach | Behavior | Purpose |
|---|---|---|
| Deterministic sequences | Execute predefined browser sub-actions without a model call for each action. | Handle known interaction patterns predictably. |
| Model-guided browser actions | Observe the screen and available elements, request an action from the model, execute it, and observe again. | Handle interactions that require interpretation of the current application state. |

Navigation and waits have direct execution paths. The executor also supports extraction, branching, loops, and explicit human-input pauses.

The model-guided loop works toward the step's intent against the live interface. It does not simply replay the recording's original cursor coordinates.

The codebase includes direct backend tool calls, but those are outside the selected UI-only business-action experiment. The `notify` step is simulated in the reviewed snapshot.

Relevant modules: `sandbox_agent/executor.py`, `browser_mode.py`, and `grounding.py`.

### 7. User-context execution through a Single-Access UI Bridge

The POC connects browser-based automation to the business user's Salesforce identity through OAuth and Salesforce's single-access login mechanism.

The user first authorizes the Salesforce connection. The backend securely stores the resulting tokens and uses refresh-token support when a new access token is needed, subject to the connection remaining valid.

To establish the agent's browser session, the backend calls `/services/oauth2/singleaccess` with the connected user's access token. It then directs the browser to the Salesforce-generated Frontdoor URL.

We describe this pattern as a **Single-Access UI Bridge**: it connects the user's authorized OAuth identity to an authenticated application session in which the agent performs the approved workflow. This is our descriptive name for the architectural pattern, not Salesforce's official feature name.

Business actions—such as editing a case or creating a task—continue through the application's user interface. The authentication endpoint establishes the session; it does not perform those business operations.

The agent operates within the connected user's Salesforce access, subject to applicable permissions and session policies. Long-lived OAuth tokens remain in the backend, while the browser holds an authenticated session that must itself be protected.

This mechanism establishes user identity. The automation platform must separately enforce which plan and actions the user has approved.

Relevant modules: `backend/app/services/frontdoor.py`, `backend/app/api/sandbox_frontdoor.py`, and `backend/app/services/oauth/`.

### 8. Backend and sandbox responsibilities

The backend manages long-lived model API keys and encrypted integration credentials. The sandbox calls backend services using a per-run token.

The Single-Access UI Bridge establishes the connected user's Salesforce browser session. The sandbox therefore holds a run token and browser-session authority, even though long-lived provider keys remain in the backend.

This separation reduces where long-lived secrets are handled. It does not, by itself, establish production-grade isolation. Token handling, endpoint protection, live-view access, and network exposure require further hardening.

Relevant modules: `backend/app/services/run_executor.py`, `services/vault.py`, `services/frontdoor.py`, and `services/sandbox/`.

### 9. Completion and exceptions

Plan steps can declare a success condition and a failure policy: pause, skip, abort, or retry.

These mechanisms express the intended behavior, but their enforcement varies across execution paths. A successful browser action does not necessarily establish that the business outcome was achieved.

The POC therefore verifies actual application state, including:

- Whether the intended record changed.
- Whether excluded records remained unchanged.
- Whether a task or record was duplicated.
- Whether missing information was surfaced.

Pause/resume support starts a new run with collected variables and a target step. It does not restore the entire previous browser state. Reliable recovery, including nested steps, remains a validation requirement.

### 10. Execution records and memory

The system collects step results, action observations, model-provided reasoning text, timing, and available usage information.

These traces support debugging and explanation. Model-generated reasoning is not independent proof that an action was correct.

Before execution, the backend can retrieve procedural and episodic hints from prior runs. After execution, reflection can produce additional stored lessons.

This is retrieval-based memory, not model retraining. Whether it improves reliability or reduces effort must be measured across comparative runs.

Relevant modules: `backend/app/services/memory/` and `backend/app/services/run_executor.py`.

### 11. Current deployment scope

The reviewed implementation uses:

- A FastAPI backend.
- A Next.js frontend with an incomplete self-service journey.
- SQL-backed persistence and local recording artifacts.
- Local Docker sandboxes.
- In-process background execution.

Runs are initiated on demand. Scheduling and event triggers are future work. Modal and Fargate runner implementations are stubs, and there is no durable execution queue.

This architecture supports investigation of the POC hypothesis. It does not yet establish unattended operation at scale.

### 12. Design rationale

**Separate interpretation from execution.**
The user can inspect and correct the inferred process before it affects application records.

**Use a structured plan between components.**
Recording interpretation and browser execution can evolve independently while sharing an explicit contract.

**Combine deterministic and model-guided actions.**
Known interactions can follow predefined actions, while variable situations can use live interpretation.

**Treat business outcomes as the measure of success.**
Execution traces and model messages help explain behavior, but application state determines whether the task was completed correctly.

**Make uncertainty visible.**
An incomplete recording, missing narration, or ambiguous rule should lead to review or clarification rather than an invented business policy.
