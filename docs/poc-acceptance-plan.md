# POC Validation Plan
## Demonstration-Guided Agentic Automation

### Objective

Evaluate whether a business user's narrated screen recording can be transformed into a reviewable plan that an agent executes through an application's user interface, without relying on application-specific APIs or MCP tools for the demonstrated business actions.

The POC uses browser-based agent execution to perform business actions through the application's user interface. Validation focuses on whether a narrated demonstration produces a reusable plan that completes those actions correctly, without application-specific APIs or MCP tools performing the business operations.

The platform still uses model APIs for interpretation and reasoning and supported authentication mechanisms to establish the user's browser session.

### Selected business process

Use a Salesforce test environment to demonstrate a case-escalation process:

1. Identify cases that meet an explicitly stated eligibility rule.
2. Update each eligible case to Escalated.
3. Create a follow-up task if an equivalent task does not already exist.
4. Leave excluded cases unchanged.
5. Pause and request clarification when required information is missing.

The exact eligibility rule, task fields, and exception behavior must be agreed upon before recording.

### What the user demonstrates

The business user records one example and explains:

- What business outcome they want.
- Why the selected case qualifies.
- Which values are examples and which rules apply across cases.
- When a case should be excluded.
- What successful completion looks like.

The system generates a plan for review. Any corrections are recorded so we can distinguish what the agent inferred correctly from what required human clarification.

### Validation scenarios

| Scenario | What we are testing | Expected result |
|---|---|---|
| Recorded example | Can the system interpret the demonstrated process? | The plan captures the intended actions and narrated rules. |
| Different eligible case | Can the plan apply beyond the original example? | The agent processes a different qualifying case without depending on the recorded case ID. |
| Excluded case | Does the agent respect the selection rule? | The case remains unchanged. |
| Already completed case | Can the automation avoid duplicate work? | No duplicate follow-up task is created. |
| Missing information | Does the agent recognize an exception? | It identifies the missing information and pauses rather than inventing a value. |
| Minor interface variation | Can execution respond to the current screen? | The agent completes the task or stops with a clear explanation, without falsely reporting success. |

### Evidence to capture

For each run, retain:

- The source recording and spoken instructions.
- The generated plan and any human corrections.
- The approved plan version.
- The execution trace and reported outcome.
- The actual before-and-after application state.

Verification must establish that the intended records changed correctly. An agent's “completed” message alone is insufficient.

### Measures

Record manual completion time, plan-review effort, execution time, human interventions, correct outcomes, duplicate actions, and model usage.

Separate one-time setup and plan-review effort from effort required on subsequent runs. Report results as observations from this experiment, not as general performance guarantees.

### POC success criteria

The POC demonstrates feasibility when:

- A fresh narrated recording produces a usable plan.
- The approved plan completes the selected workflow through the UI.
- It applies successfully to a different eligible record.
- Excluded records remain unchanged.
- Reruns do not create duplicate work.
- Missing information produces an explicit exception.

Report each criterion as passed, failed, or not tested. Partial results and failures are part of the findings.

### Scope boundaries

This experiment evaluates a bounded workflow in a controlled test environment. It does not establish production readiness, universal application compatibility, unattended operation at scale, or automatic recovery from every failure.

Scheduling, broader application coverage, and production hosting are subsequent milestones.
