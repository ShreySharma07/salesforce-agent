// app/(marketing)/AgentRun.tsx
// One persistent sandbox browser that changes with the "How it works" stage:
// recording → plan → approval → agent run → verification. The run stage
// mirrors the real agent (sandbox_agent/browser_mode.py + grounding.py):
// elements are grounded as `#ref [role] 'name'`, the agent thinks, acts,
// observes, and verifies. Sample data only; no real org or logos.
"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { motion, useInView, useReducedMotion } from "framer-motion";
import { ruleText, useStory } from "./story";

const STAGES = ["Proposal", "Negotiation", "Closed Won"];
const PREV = ["Qualification", "Needs Analysis"];

type Line = { k: string; t: string };
const LOG: Line[] = [
  { k: "step", t: "3/6  Update Opportunity Stage" },
  { k: "observe", t: "screenshot 1440×900 · 41 elements grounded" },
  { k: "ground", t: "#14 [tab] 'Negotiation'  region=main" },
  { k: "think", t: "Stage reads Proposal; the plan needs Negotiation." },
  { k: "act", t: "click #14  →  Negotiation selected" },
  { k: "ground", t: "#15 [button] 'Mark as Current Stage'" },
  { k: "act", t: "click #15" },
  { k: "observe", t: "toast: “Stage changed successfully.”" },
  { k: "verify", t: "Stage = Negotiation  ✓  step 3 done" },
];
// Which log line each tick reveals up to, and what the screen shows.
const TICKS = [1, 2, 3, 4, 5, 6, 7, 8];
const HOLD = 3; // extra ticks before the loop restarts

function useScale(designW: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setScale(Math.min(1, el.clientWidth / designW)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [designW]);
  return { ref, scale };
}

export function AgentRun({ stage }: { stage: number }) {
  const { answer } = useStory();
  const reduce = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  const inView = useInView(box, { amount: 0.3 });
  const { ref: fit, scale } = useScale(760);
  const screen = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLSpanElement>(null);
  const [tick, setTick] = useState(reduce ? TICKS.length + HOLD : 0);
  const [rect, setRect] = useState({ x: 0, y: 0, w: 0, h: 0 });

  const running = stage === 3;
  const t = running ? tick : stage > 3 ? 99 : 0;

  // Drive the run loop only while visible and on the run stage.
  useEffect(() => {
    if (!running || !inView || reduce) return;
    const id = window.setInterval(() => {
      if (document.querySelector(".rq")?.hasAttribute("data-motion-stopped")) return;
      setTick((k) => (k >= TICKS.length + HOLD ? 0 : k + 1));
    }, 950);
    return () => window.clearInterval(id);
  }, [running, inView, reduce]);

  // Ground the reticle on the element the current tick targets.
  const target = t >= 5 && t < 7 ? btn : chip;
  useLayoutEffect(() => {
    const s = screen.current, el = target.current;
    if (!s || !el) return;
    const a = s.getBoundingClientRect(), b = el.getBoundingClientRect();
    setRect({ x: (b.left - a.left) / scale - 6, y: (b.top - a.top) / scale - 6, w: b.width / scale + 12, h: b.height / scale + 12 });
  }, [target, scale, t, stage]);

  const stageNow = t >= 6 || stage > 3 ? "Negotiation" : "Proposal";
  const selected = t >= 4 ? "Negotiation" : stageNow;
  const shown = running ? LOG.slice(0, Math.min(t + 1, LOG.length)) : stage > 3 ? LOG : [];
  const showReticle = running && t >= 2 && t < 7;
  const som = running && t === 1;

  const counter =
    stage < 3 ? null
    : stage === 3 ? { n: 3, label: "Update Opportunity Stage", state: t >= 8 ? "done" : "running" }
    : { n: 6, label: "Verify the records", state: "done" };

  return (
    <div ref={box} className="rq-run" data-stage={stage}>
      <div className="rq-chrome">
        <div className="rq-chrome-top">
          <span className="rq-chrome-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="rq-chrome-tab">
            {stage === 0 && <span className="rq-rec" aria-hidden="true" />}
            Northwind Renewal | Opportunity
          </span>
          <span className="rq-chrome-badge">{stage === 0 ? "Recording" : "Sandbox · headless Chromium"}</span>
        </div>
        <div className="rq-chrome-url">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
          example.lightning.force.com/lightning/r/Opportunity/006Rq000001/view
        </div>
      </div>

      <div ref={fit} className="rq-run-fit" style={{ height: 470 * scale }}>
        <div ref={screen} className="rq-lx" style={{ transform: `scale(${scale})` }} data-som={som || undefined}>
          <div className="rq-lx-bar">
            <span className="rq-lx-app">Sales</span>
            {["Home", "Opportunities", "Cases", "Accounts", "Reports"].map((n, i) => (
              <span key={n} className={i === 1 ? "rq-lx-tab is-on" : "rq-lx-tab"}>{n}</span>
            ))}
          </div>
          <div className="rq-lx-head">
            <span className="rq-lx-icon" />
            <div>
              <p className="rq-lx-kind">Opportunity</p>
              <p className="rq-lx-title">Northwind Renewal</p>
            </div>
            <span className="rq-lx-act" data-ground="#9 [button] 'Edit'">Edit</span>
          </div>
          <div className="rq-lx-fields">
            <div><p>Amount</p><b>$285,000.00</b></div>
            <div><p>Close date</p><b>12/19/2026</b></div>
            <div><p>Account</p><b>Northwind (example)</b></div>
            <div><p>Owner</p><b>Jordan Lee</b></div>
          </div>
          <div className="rq-lx-pathrow">
            <div className="rq-lx-path">
              {PREV.map((p) => <span key={p} className="rq-lx-step is-done">{p}</span>)}
              {STAGES.map((p, i) => (
                <span
                  key={p}
                  ref={p === "Negotiation" ? chip : undefined}
                  className={`rq-lx-step${i < STAGES.indexOf(stageNow) ? " is-done" : ""}${p === stageNow ? " is-current" : ""}${p === selected && p !== stageNow ? " is-selected" : ""}`}
                  data-ground={p === "Negotiation" ? "#14 [tab] 'Negotiation'" : undefined}
                >{p}</span>
              ))}
            </div>
            <span ref={btn} className="rq-lx-mark" data-ground="#15 [button] 'Mark as Current Stage'">Mark as Current Stage</span>
          </div>
          <div className="rq-lx-body">
            <div className="rq-lx-card">
              <p className="rq-lx-h">Details</p>
              <dl>
                <dt>Stage</dt><dd>{stageNow}</dd>
                <dt>Next step</dt><dd>Unblock renewal case</dd>
                <dt>Probability</dt><dd>{stageNow === "Negotiation" ? "75%" : "60%"}</dd>
              </dl>
            </div>
            <div className="rq-lx-card">
              <p className="rq-lx-h">Related case</p>
              <p className="rq-lx-case">00001042 · Renewal blocked by login loop</p>
              <p className="rq-lx-pills">
                <span className={stage >= 4 ? "rq-lx-pill is-hi" : "rq-lx-pill"}>Priority: {stage >= 4 ? "High" : "Medium"}</span>
                {stage >= 4 && <span className="rq-lx-pill is-esc">Escalated</span>}
              </p>
              <span className="rq-lx-input" data-ground="#22 [textbox] 'Subject' (type here)" data-fill>Follow-up: renewal call</span>
            </div>
          </div>

          {/* Stage overlays */}
          {stage === 0 && (
            <div className="rq-ov rq-ov--rec">
              <p className="rq-ov-say"><b>You said, 0:31</b> “This one’s on a $285K deal, so it goes first.”</p>
              <div className="rq-ov-frames" aria-hidden="true">{Array.from({ length: 7 }, (_, i) => <span key={i} className={i === 3 ? "is-key" : ""} />)}</div>
            </div>
          )}
          {stage === 1 && (
            <div className="rq-ov rq-ov--plan">
              <p className="rq-ov-h">Plan · 6 steps</p>
              <ol>
                {["Open the urgent case", "Open its opportunity", "Update Opportunity Stage", "Set case priority to High, escalate", "Create a follow-up task", "Verify the records"].map((s, i) => (
                  <li key={s}><span>{i + 1}</span>{s}</li>
                ))}
              </ol>
            </div>
          )}
          {stage === 2 && (
            <div className="rq-ov rq-ov--modal">
              <div className="rq-ov-dialog">
                <p className="rq-ov-h">Approve this plan?</p>
                <p className="rq-ov-q">Should every urgent case get High priority?</p>
                <p className="rq-ov-a">{ruleText(answer)}</p>
                <span className="rq-ov-approve">Approve plan</span>
              </div>
            </div>
          )}
          {stage === 3 && t >= 6 && t < 9 && <p className="rq-lx-toast">Stage changed successfully.</p>}
          {stage === 4 && (
            <div className="rq-ov rq-ov--verify">
              <p className="rq-ov-h">Verified in the app</p>
              <ul>
                {["Opportunity stage reads Negotiation", "Case priority is High and escalated", "Exactly one follow-up task exists", "Cases that didn’t qualify are unchanged"].map((c) => (
                  <li key={c}><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>{c}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Grounding reticle */}
          {running && (
            <motion.div
              className="rq-reticle"
              aria-hidden="true"
              initial={false}
              animate={{ x: rect.x, y: rect.y, width: rect.w, height: rect.h, opacity: showReticle ? 1 : 0 }}
              transition={{ duration: reduce ? 0 : 0.55, ease: [0.65, 0, 0.35, 1] }}
            >
              <span className="rq-reticle-label" data-side={t >= 5 ? "end" : "start"}>{t >= 5 ? "#15 [button] 'Mark as Current Stage'" : "#14 [tab] 'Negotiation'"}</span>
              {(t === 4 || t === 6) && <span className="rq-reticle-click" />}
            </motion.div>
          )}
        </div>
      </div>

      <div className="rq-run-foot">
        {counter ? (
          <p className="rq-counter" data-state={counter.state}>
            <span className="num">Step {counter.n}/6</span>{counter.label}
            <span className="rq-counter-state">{counter.state === "done" ? "Done" : "Running"}</span>
          </p>
        ) : (
          <p className="rq-counter" data-state="idle"><span>{stage === 0 ? "Capturing" : "Waiting"}</span>{stage === 0 ? "Narration and key frames" : stage === 1 ? "Plan drafted, not yet approved" : "Nothing runs until you approve"}</p>
        )}
        <pre className="rq-log" aria-live="off">
          {shown.length === 0
            ? <span className="rq-log-dim">{stage === 0 ? "[record]  frames 0:00–1:00 · narration transcribing…" : stage === 1 ? "[plan]    6 steps drafted from recording" : "[review]  awaiting approval"}</span>
            : shown.map((l, i) => (
                <span key={i} className={`rq-log-line rq-log--${l.k}`}><b>{`[${l.k}]`.padEnd(10)}</b>{l.t}{"\n"}</span>
              ))}
          {stage === 4 && <span className="rq-log-line rq-log--verify"><b>{"[verify]".padEnd(10)}</b>4/4 checks passed · run complete{"\n"}</span>}
          {running && t < TICKS.length && <span className="rq-caret" aria-hidden="true" />}
        </pre>
      </div>
    </div>
  );
}
