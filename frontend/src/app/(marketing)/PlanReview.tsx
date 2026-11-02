// app/(marketing)/PlanReview.tsx
// The plan panel: a plan drafted from a recording that asks one
// question before it can be approved. The answer is shared with the rest of
// the page through the story context.
"use client";

import { useStory } from "./story";

const STEPS = [
  { text: "Open new cases in the queue" },
  { text: "Find the case on the $285K deal" },
  { text: "Set priority to High and escalate", asks: true },
  { text: "Create and assign a follow-up task" },
];

export function PlanReview() {
  const { answer, setAnswer, approved, setApproved } = useStory();

  return (
    <figure className="rq-plan" aria-label="Example plan drafted from a recording">
      <header className="rq-plan-head">
        <h2 className="rq-plan-title">Escalate urgent cases on open deals</h2>
        <div className="rq-plan-metarow">
          <p className="rq-plan-meta"><span className="rq-who rq-who--you">Drafted from your recording</span></p>
          <p className="rq-plan-state" role="status" data-approved={approved}>
            {approved ? "Approved · ready to run" : "Needs review"}
          </p>
        </div>
      </header>

      <ol className="rq-plan-steps">
        {STEPS.map((s, i) => (
          <li key={s.text} className="rq-step" style={{ "--i": i } as React.CSSProperties}>
            <span className="rq-step-n num" aria-hidden="true">{i + 1}</span>
            <div className="rq-step-body">
              <p className="rq-step-text">{s.text}</p>
              {s.asks && (
                <fieldset className="rq-ask" disabled={approved}>
                  <legend className="rq-ask-q">You chose High here. Should every urgent case get it?</legend>
                  <div className="rq-seg">
                    <input type="radio" name="rule" id="rule-every" checked={answer === "every"} onChange={() => setAnswer("every")} />
                    <label htmlFor="rule-every">Every urgent case</label>
                    <input type="radio" name="rule" id="rule-once" checked={answer === "once"} onChange={() => setAnswer("once")} />
                    <label htmlFor="rule-once">Only this one</label>
                  </div>
                </fieldset>
              )}
            </div>
          </li>
        ))}
      </ol>

      <footer className="rq-plan-foot">
        <button
          type="button"
          className="rq-btn rq-btn--plan"
          aria-disabled={answer === null || approved}
          onClick={() => { if (answer !== null) setApproved(true); }}
        >
          {approved ? "Plan approved" : "Approve plan"}
        </button>
        <p className="rq-plan-hint">
          {approved
            ? "Approved. Next, it becomes a tool."
            : answer === null
              ? "Answer the question to approve."
              : "Nothing runs until you approve."}
        </p>
      </footer>
    </figure>
  );
}
