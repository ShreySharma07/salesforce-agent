# Demonstration-Guided Agentic Automation

**Exploring how a narrated screen recording can become a reviewable plan that an agent executes through an application's user interface.**

## The business problem

Business processes often depend on applications that employees operate manually. Suitable APIs or MCP tools may be unavailable, incomplete, or inaccessible—or may cover only part of the workflow.

Employees still need to navigate screens, interpret information, apply business rules, and update records.

This POC explores whether a business user can teach an agent a bounded process by demonstrating it and explaining its intent.

## Our hypothesis

A screen recording shows **how the work is performed**. Spoken narration explains **why it is performed**, which records qualify, and what outcome is expected.

Together, these inputs may provide enough context to generate a reusable plan that a user reviews and approves before an agent executes it.

The goal is to move beyond replaying recorded clicks toward completing an intended task against the application's current state.

## How it works

1. **Demonstrate:** Record a business process with spoken explanation.
2. **Interpret:** Extract visual observations and narration from the recording.
3. **Plan:** Generate a structured goal, inferred rules, execution steps, and completion conditions.
4. **Review:** Inspect the plan, correct misunderstandings, and approve it.
5. **Execute:** Run the approved plan in a browser-based agent environment.
6. **Verify:** Compare the agent's reported outcome with the actual application state.

The implementation processes audio and sampled video frames in stages. It does not send the entire recording through one model call.

## Why UI-based execution?

The main experiment tests automation where suitable application integrations are unavailable.

The agent performs business operations through the application interface: opening records, editing fields, submitting forms, and checking results.

Model APIs support interpretation and reasoning, and backend services coordinate execution and authentication. Application-specific APIs or MCP tools do not perform the business operations in the selected experiment.

The codebase also contains direct tool-call capabilities. Those are outside the scope of this UI-based experiment.

## Architecture

| Component | Responsibility |
|---|---|
| User interface | Present plans for review and provide access to the available application screens. The complete self-service journey is still being developed. |
| Processing and coordination backend | Ingest recordings, invoke models, generate and store plans, handle approval, and manage runs. |
| Agent sandbox | Operate Chromium using deterministic action sequences and model-guided browser interaction. |
| Execution records and memory | Store outcomes and traces, and retrieve relevant hints from prior runs. |

Long-lived provider credentials and model API keys are managed by the backend. The sandbox receives a run token and can hold an authenticated browser session. These boundaries require further hardening before remote hosting.

For Salesforce, the **Single-Access UI Bridge** uses the connected business user's OAuth identity and a Salesforce-generated Frontdoor URL to establish the browser session. Business actions remain UI-based. This is our architectural name for the pattern, not Salesforce's official feature name. See the implementation architecture for details.

## Initial experiment

The proposed validation workflow uses a Salesforce test environment:

- Identify cases that meet a stated eligibility rule.
- Update eligible cases to Escalated.
- Create a follow-up task when an equivalent task does not already exist.
- Leave excluded cases unchanged.
- Surface missing information or ambiguity.

Validation includes a different eligible record, a duplicate-prevention rerun, and an exception case.

Salesforce is the initial test environment. Broader application compatibility has not been established.

## Current status

**Experimental POC under validation.**

The reviewed implementation includes recording processing, plan generation and correction, approval, on-demand browser execution, and execution traces.

Current limitations include:

- An incomplete end-to-end business-user interface.
- Manual run initiation; scheduling and event triggers remain future work.
- Recovery and outcome-verification paths that need additional validation.
- A simulated `notify` step.
- Local Docker execution; cloud runner implementations are stubs.
- Security hardening required before remotely accessible deployment.

No general reliability, performance, or production-readiness claims are made. This description is based on the supplied source snapshot; maintainers should reconcile it with subsequent implementation changes before publication.

## Evaluation

We evaluate correct business outcomes, excluded records left unchanged, duplicate prevention, human interventions, elapsed time, and model usage.

A run is not considered successful solely because the agent reports completion. Results must be checked against application state.

Measured results and a demonstration will be added after validation.

## Documentation

- [Business problem and design](docs/recording-to-automation-design.md)
- [POC validation plan](docs/poc-acceptance-plan.md)
- [Implementation architecture](Architecture.md)
- [Existing development instructions — pending revalidation](docs/development-setup.md)

## Contributors

Proposed attribution for confirmation before publication:

- **Sumit Paliwal (@supaliwa):** Original automation concept, business problem framing, and POC design and evaluation documentation.
- **Shrey (@ShreySharma07):** Implementation and engineering development.

These descriptions recognize project roles; GitHub's automatically generated contributor list reflects attributed repository commits.
