// app/(marketing)/AgentRun.tsx
// One persistent sandbox browser that changes with the "How it works" stage:
// recording → plan → approval → agent run → verification. The run mirrors the
// real agent (sandbox_agent/browser_mode.py + grounding.py): elements are
// grounded as `#ref [role] 'name'`, the agent thinks, acts, observes and
// verifies. The run is driven by scroll (`tick`), so every frame is
// deterministic. Sample data only; no real org or logos.
"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ruleText, useStory } from "./story";

const STAGES = ["Proposal", "Negotiation", "Closed Won"];
const PREV = ["Qualification", "Needs Analysis"];

type Line = { k: string; t: string };
export const RUN_TICKS = 9;
const RUN: Line[] = [
  { k: "step", t: "3/6  Update Opportunity Stage" },
  { k: "observe", t: "screenshot 1440×900 · 41 elements grounded" },
  { k: "ground", t: "#14 [tab] 'Negotiation'  region=main" },
  { k: "think", t: "Stage reads Proposal; the plan needs Negotiation." },
  { k: "act", t: "click #14  →  Negotiation selected" },
  { k: "ground", t: "#15 [button] 'Mark as Current Stage'" },
  { k: "act", t: "click #15" },
  { k: "observe", t: "toast “Stage changed successfully.”" },
  { k: "verify", t: "Stage = Negotiation  ✓  step 3 done" },
];
const VERIFY: Line[] = [
  { k: "step", t: "6/6  Verify the records" },
  { k: "observe", t: "Opportunity: Stage = Negotiation" },
  { k: "observe", t: "Case 00001042: Priority = High, Escalated = true" },
  { k: "observe", t: "Follow-up tasks on case: 1 (expected 1)" },
  { k: "observe", t: "Other open cases: unchanged (12 of 12)" },
  { k: "verify", t: "4/4 checks passed · run complete" },
];

function preLines(stage: number, answer: string, approvedByYou: boolean): Line[] {
  if (stage === 0) return [
    { k: "record", t: "capturing screen 1440×900 · narration on" },
    { k: "audio", t: "“This one’s on a $285K deal, so it goes first.”" },
    { k: "frames", t: "scene change at 0:31 · key frame kept" },
  ];
  if (stage === 1) return [
    { k: "align", t: "narration segments matched to key frames" },
    { k: "plan", t: "6 steps drafted in plain language" },
    { k: "plan", t: "1 open question: is High a rule or a one-off?" },
  ];
  return [
    { k: "review", t: `answer: ${answer}` },
    { k: "review", t: approvedByYou ? "plan approved by you" : "plan approved by a reviewer" },
    { k: "run", t: "fresh sandbox browser · one-time pass issued" },
  ];
}

export function AgentRun({ stage, tick }: { stage: number; tick: number }) {
  const { answer, approved } = useStory();
  const reduce = useReducedMotion();
  const fit = useRef<HTMLDivElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLSpanElement>(null);
  const [compact, setCompact] = useState(false);
  const [scale, setScale] = useState(1);
  const [rects, setRects] = useState({ chip: { x: 0, y: 0, w: 0, h: 0 }, btn: { x: 0, y: 0, w: 0, h: 0 } });

  const designW = compact ? 380 : 760;
  const designH = compact ? 430 : 470;

  useLayoutEffect(() => {
    const el = fit.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      const c = w < 560;
      setCompact(c);
      setScale(Math.min(1, w / (c ? 380 : 760)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const running = stage === 3;
  const t = running ? Math.max(0, Math.min(tick, RUN_TICKS)) : stage > 3 ? 99 : 0;
  const stageNow = t >= 6 || stage > 3 ? "Negotiation" : "Proposal";
  const selected = t >= 4 ? "Negotiation" : stageNow;

  // Measure the two grounded targets in unscaled screen coordinates.
  useLayoutEffect(() => {
    const s = screen.current;
    if (!s || !chip.current || !btn.current) return;
    const a = s.getBoundingClientRect();
    const m = (el: HTMLElement) => { const b = el.getBoundingClientRect(); return { x: (b.left - a.left) / scale - 4, y: (b.top - a.top) / scale - 4, w: b.width / scale + 8, h: b.height / scale + 8 }; };
    setRects({ chip: m(chip.current), btn: m(btn.current) });
  }, [scale, compact, stage, stageNow]);

  const onBtn = t >= 5 && t < 8;
  const r = onBtn ? rects.btn : rects.chip;
  const showReticle = running && t >= 2 && t < 8;
  const som = running && t === 1;
  const clicking = running && (t === 4 || t === 6);

  const shown: Line[] =
    stage < 3 ? preLines(stage, ruleText(answer), approved)
    : stage === 3 ? RUN.slice(0, Math.min(t + 1, RUN.length))
    : VERIFY;

  const counter =
    stage < 3 ? { n: "", label: stage === 0 ? "Capturing narration and key frames" : stage === 1 ? "Plan drafted from the recording" : approved ? "Approved by you" : "Approved by a reviewer", state: "idle" }
    : stage === 3 ? { n: "Step 3/6", label: "Update Opportunity Stage", state: t >= 8 ? "done" : "running" }
    : { n: "Step 6/6", label: "Verify the records", state: "done" };

  const steps = compact ? STAGES : [...PREV, ...STAGES];
  const order = [...PREV, ...STAGES];

  return (
    <div className="rq-run" data-stage={stage}>
      <div className="rq-chrome">
        <div className="rq-chrome-top">
          <span className="rq-chrome-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="rq-chrome-tab">
            {stage === 0 && <span className="rq-rec" aria-hidden="true" />}
            Northwind Renewal | Opportunity
          </span>
          <span className="rq-chrome-badge">{stage === 0 ? "Recording" : "Isolated browser"}</span>
        </div>
        <div className="rq-chrome-url">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
          <span>example.lightning.force.com/lightning/r/Opportunity/006Rq000001/view</span>
        </div>
      </div>

      <div ref={fit} className="rq-run-fit" style={{ height: designH * scale }}>
        <div ref={screen} className={compact ? "rq-lx is-compact" : "rq-lx"} style={{ width: designW, height: designH, transform: `scale(${scale})` }} data-som={som || undefined}>
          <div className="rq-lx-bar">
            <span className="rq-lx-app">Sales</span>
            {(compact ? ["Opportunities", "Cases"] : ["Home", "Opportunities", "Cases", "Accounts", "Reports"]).map((n) => (
              <span key={n} className={n === "Opportunities" ? "rq-lx-tab is-on" : "rq-lx-tab"}>{n}</span>
            ))}
          </div>
          <div className="rq-lx-head">
            <span className="rq-lx-icon" />
            <div>
              <p className="rq-lx-kind">Opportunity</p>
              <p className="rq-lx-title">Northwind Renewal</p>
            </div>
            <span className="rq-lx-act" data-ground="#9">Edit</span>
          </div>
          <div className="rq-lx-fields">
            <div><p>Amount</p><b>$285,000.00</b></div>
            <div><p>Close date</p><b>12/19/2026</b></div>
            {!compact && <div><p>Account</p><b>Northwind (example)</b></div>}
            {!compact && <div><p>Owner</p><b>Jordan Lee</b></div>}
          </div>
          <div className="rq-lx-pathrow">
            <div className="rq-lx-path">
              {steps.map((p) => (
                <span
                  key={p}
                  ref={p === "Negotiation" ? chip : undefined}
                  className={`rq-lx-step${order.indexOf(p) < order.indexOf(stageNow) ? " is-done" : ""}${p === stageNow ? " is-current" : ""}${p === selected && p !== stageNow ? " is-selected" : ""}`}
                  data-ground={p === "Negotiation" ? "#14" : undefined}
                >{p}</span>
              ))}
            </div>
            <span ref={btn} className="rq-lx-mark" data-ground="#15">Mark as Current Stage</span>
          </div>
          <div className="rq-lx-body">
            {!compact && (
              <div className="rq-lx-card">
                <p className="rq-lx-h">Details</p>
                <dl>
                  <dt>Stage</dt><dd>{stageNow}</dd>
                  <dt>Next step</dt><dd>Unblock renewal case</dd>
                  <dt>Probability</dt><dd>{stageNow === "Negotiation" ? "75%" : "60%"}</dd>
                </dl>
              </div>
            )}
            <div className="rq-lx-card">
              <p className="rq-lx-h">Related case</p>
              <p className="rq-lx-case">00001042 · Renewal blocked by login loop</p>
              <p className="rq-lx-pills">
                <span className={stage >= 4 ? "rq-lx-pill is-hi" : "rq-lx-pill"}>Priority: {stage >= 4 ? "High" : "Medium"}</span>
                {stage >= 4 && <span className="rq-lx-pill is-esc">Escalated</span>}
              </p>
              <span className="rq-lx-input" data-ground="#22" data-fill>Follow-up: renewal call</span>
            </div>
          </div>

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
                <p className="rq-ov-h">{approved ? "You approved this plan" : "Plan approved"}</p>
                <p className="rq-ov-q">Is High a rule for every urgent case?</p>
                <p className="rq-ov-a">{ruleText(answer)}</p>
                <span className="rq-ov-ok">{approved ? "Approved by you" : "Approved by a reviewer"}</span>
              </div>
            </div>
          )}
          {running && t >= 6 && t < 9 && <p className="rq-lx-toast">Stage changed successfully.</p>}
          {stage === 4 && (
            <div className="rq-ov rq-ov--verify">
              <p className="rq-ov-h">Verified in the app</p>
              <ul>
                {["Opportunity stage reads Negotiation", "Case priority is High and escalated", "Exactly one follow-up task", "Other cases unchanged"].map((c) => (
                  <li key={c}><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>{c}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Grounding reticle(s): one live target during the run, both as evidence when verifying. */}
          {running && (
            <motion.div
              className="rq-reticle"
              aria-hidden="true"
              initial={false}
              animate={{ x: r.x, y: r.y, width: r.w, height: r.h, opacity: showReticle ? 1 : 0 }}
              transition={{ duration: reduce ? 0 : 0.45, ease: [0.65, 0, 0.35, 1] }}
            >
              <span className="rq-reticle-tag">{onBtn ? "#15" : "#14"}</span>
              {clicking && <span className="rq-reticle-click" />}
            </motion.div>
          )}
          {running && (
            <motion.svg
              className="rq-ghost"
              viewBox="0 0 24 24"
              width="22"
              height="22"
              aria-hidden="true"
              initial={false}
              animate={{ x: r.x + r.w / 2, y: r.y + r.h / 2, opacity: showReticle ? 1 : 0, scale: clicking ? 0.85 : 1 }}
              transition={{ duration: reduce ? 0 : 0.6, ease: [0.65, 0, 0.35, 1], delay: reduce ? 0 : 0.08 }}
            >
              <path d="M3 2l7.5 19 2.6-7.6L21 10.8z" />
            </motion.svg>
          )}
          {stage === 4 && (["chip", "btn"] as const).map((k) => (
            <div key={k} className="rq-reticle is-static" aria-hidden="true" style={{ left: rects[k].x, top: rects[k].y, width: rects[k].w, height: rects[k].h }}>
              <span className="rq-reticle-tag">{k === "chip" ? "#14" : "#15"}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="rq-run-foot">
        <p className="rq-counter" data-state={counter.state} aria-live="polite">
          {counter.n && <span className="num">{counter.n}</span>}
          {counter.label}
          {counter.state !== "idle" && <span className="rq-counter-state">{counter.state === "done" ? "Done" : "Running"}</span>}
        </p>
        <pre className="rq-log">
          {shown.map((l, i) => (
            <span key={`${stage}-${i}`} className={`rq-log-line rq-log--${l.k}`}><b>{`[${l.k}]`.padEnd(10)}</b>{l.t}{"\n"}</span>
          ))}
          {running && t < RUN_TICKS - 1 && <span className="rq-caret" aria-hidden="true" />}
        </pre>
      </div>
    </div>
  );
}
