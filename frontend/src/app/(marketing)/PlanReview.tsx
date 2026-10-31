// app/(marketing)/PlanReview.tsx
// The hero's product panel: a plan drafted from a recording that asks one
// question before it can be approved. Approving marks each step as run.
"use client";

import { useState } from "react";

type Answer = "every" | "once" | null;

const STEPS = [
  { text: "Open new cases in the queue", at: "0:04" },
  { text: "Find the case on the $285K deal", at: "0:31" },
  { text: "Set priority to High and escalate", at: "0:52", asks: true },
  { text: "Create and assign a follow-up task", at: "1:18" },
];

export function PlanReview() {
  const [answer, setAnswer] = useState<Answer>(null);
  const [approved, setApproved] = useState(false);

  return (
    <figure className="rq-plan" aria-label="Example plan drafted from a recording">
      <header className="rq-plan-head">
        <div>
          <h2 className="rq-plan-title">Escalate urgent cases on open deals</h2>
          <p className="rq-plan-meta"><span className="rq-who rq-who--you">Steps from your recording</span></p>
        </div>
        <p className="rq-plan-state" role="status" data-approved={approved}>
          {approved ? "Approved" : "Needs review"}
        </p>
      </header>

      <ol className="rq-plan-steps">
        {STEPS.map((s, i) => (
          <li key={s.text} className="rq-step" style={{ "--i": i } as React.CSSProperties}>
            <span className="rq-step-n num" aria-hidden="true">{i + 1}</span>
            <div className="rq-step-body">
              <p className="rq-step-text">{s.text}</p>
              <p className="rq-step-src">
                <span className="rq-who rq-who--you num" aria-label={`You, at ${s.at}`}>{s.at}</span>
                {approved && <span className="rq-who rq-who--agent" data-on="true">Agent done</span>}
              </p>
              {s.asks && (
                <fieldset className="rq-ask" disabled={approved}>
                  <legend className="rq-ask-q">
                    You chose High. Should every urgent case get it?
                  </legend>
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
            ? answer === "every"
              ? "Saved as a rule: urgent cases get High."
              : "Saved as a one-off: priority stays your call."
            : answer === null
              ? "Answer to approve."
              : "Nothing runs until you approve."}
        </p>
      </footer>
    </figure>
  );
}
