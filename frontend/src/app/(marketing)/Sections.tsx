// app/(marketing)/Sections.tsx
// Everything below the intro hero, telling one story with one task whose state
// only moves forward: draft → approved (what it does) → tool (impact, connect)
// → running → verified (how it works).
"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { useInView, useMotionValueEvent, useReducedMotion, useScroll } from "framer-motion";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { ruleText, useStory } from "./story";
import { AmbientMotion } from "./AmbientMotion";
import { AgentRun, RUN_TICKS } from "./AgentRun";
import { PlanReview } from "./PlanReview";

const VIDEO_ID = "gEhxQjRQ6XI";
const DEMO_URL = `https://youtu.be/${VIDEO_ID}`;
const PAPER_URL = "/repliq-white-paper.pdf";

/* ------------------------------------------------------- the task's states */

const LIFECYCLE = ["Draft", "Approved", "Tool", "Running", "Verified"] as const;
type Life = (typeof LIFECYCLE)[number];

function Lifecycle({ state }: { state: Life }) {
  const at = LIFECYCLE.indexOf(state);
  return (
    <ol className="rq-life" aria-label={`Task status: ${state}`}>
      {LIFECYCLE.map((l, i) => (
        <li key={l} data-on={i <= at || undefined} data-now={i === at || undefined}>{l}</li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------ what it does */

function DemoVideo() {
  const [playing, setPlaying] = useState(false);
  const [thumbOk, setThumbOk] = useState(true);
  const imgRef = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const img = imgRef.current;
    if (img && img.complete && img.naturalWidth === 0) setThumbOk(false);
  }, []);
  return (
    <figure className="rq-video">
      <div className="rq-video-frame">
        {playing ? (
          <iframe
            src={`https://www.youtube-nocookie.com/embed/${VIDEO_ID}?autoplay=1&rel=0`}
            title="Repliq demo: a support case on a $285K deal, recorded once and run by the agent"
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
          />
        ) : (
          <button type="button" className="rq-video-poster" onClick={() => setPlaying(true)} aria-label="Play the 60-second demo">
            {thumbOk && (
              // eslint-disable-next-line @next/next/no-img-element
              <img ref={imgRef} src={`https://i.ytimg.com/vi/${VIDEO_ID}/maxresdefault.jpg`} alt="" onError={() => setThumbOk(false)} />
            )}
            <span className="rq-video-play" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" height="20"><path d="M8 5.5v13l10.5-6.5z" /></svg></span>
            <span className="rq-video-len">Demo · 60 seconds</span>
          </button>
        )}
      </div>
      <figcaption className="rq-caption">The real demo: a support case on a $285K deal, recorded once, then run by the agent.</figcaption>
    </figure>
  );
}

function WhatWeDo() {
  return (
    <section id="what" className="rq-what" aria-labelledby="what-h">
      <div className="container rq-what-grid">
        <div className="rq-what-copy">
          <h2 id="what-h" className="rq-h2 rq-verbs"><span>Say it.</span><span>Show it.</span><span>Approve it.</span></h2>
          <ol className="rq-beats">
            <li><b>Say it</b> Narrate the task as you work, like you’d explain it to a new hire.</li>
            <li><b>Show it</b> Repliq lines up what you said with what you clicked, and drafts a plan.</li>
            <li><b>Approve it</b> Anything it can’t tell becomes a question. Try it on the plan.</li>
          </ol>
          <DemoVideo />
        </div>
        <div className="rq-what-plan">
          <PlanReview />
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ impact */

const WEEK: { day: string; tiles: [string, string, string, string][] }[] = [
  { day: "Mon", tiles: [["Renewal blocked, $285K", "9:12", "7 min", "PL"], ["SSO outage, 40 seats", "11:40", "12 min", "MR"], ["Refund over limit", "16:05", "5 min", "AJ"]] },
  { day: "Tue", tiles: [["Login loop, enterprise", "8:47", "9 min", "PL"], ["Data export failing", "13:20", "6 min", "SK"], ["Invoice disputed", "15:55", "8 min", "MR"]] },
  { day: "Wed", tiles: [["Renewal at risk", "10:02", "7 min", "AJ"], ["API keys revoked", "12:31", "11 min", "PL"], ["Seat limit reached", "17:10", "4 min", "SK"]] },
  { day: "Thu", tiles: [["Contract stuck in legal", "9:30", "10 min", "MR"], ["Payment failed twice", "14:15", "6 min", "AJ"], ["Outage reported", "16:48", "9 min", "PL"]] },
  { day: "Fri", tiles: [["Renewal blocked again", "8:55", "7 min", "SK"], ["Admin locked out", "11:05", "5 min", "MR"], ["Billing mismatch", "15:30", "8 min", "AJ"]] },
];
const FEED = [["9:12", "Renewal blocked, $285K"], ["11:40", "SSO outage, 40 seats"], ["16:05", "Refund over limit"]];

function Impact() {
  const { answer } = useStory();
  const pin = useRef<HTMLDivElement>(null);
  const [animated, setAnimated] = useState(false);

  useLayoutEffect(() => {
    gsap.registerPlugin(ScrollTrigger);
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const root = pin.current;
      if (!root) return;
      setAnimated(true);
      const week = root.querySelector<HTMLElement>(".rq-week")!;
      const card = root.querySelector<HTMLElement>(".rq-fold-card")!;
      const feed = gsap.utils.toArray<HTMLElement>(".rq-feed li", root);
      const tiles = gsap.utils.toArray<HTMLElement>(".rq-tile", root);
      const pull = (el: HTMLElement, axis: "x" | "y") => {
        const a = el.getBoundingClientRect(), c = week.getBoundingClientRect();
        return axis === "x" ? (c.left + c.width / 2 - (a.left + a.width / 2)) * 0.08 : (c.top + c.height / 2 - (a.top + a.height / 2)) * 0.08;
      };
      const tl = gsap.timeline({
        scrollTrigger: { trigger: root, start: "top 16%", end: "+=120%", pin: true, scrub: 0.6, invalidateOnRefresh: true },
      });
      tl.to(tiles, {
        x: (_i: number, el: HTMLElement) => pull(el, "x"),
        y: (_i: number, el: HTMLElement) => pull(el, "y"),
        scale: 0.96, opacity: 0.14, filter: "blur(3px)", ease: "power2.inOut", stagger: { each: 0.02, from: "center" }, duration: 1,
      })
        .to(week, { "--vig": "62%", ease: "none", duration: 1 }, 0)
        .fromTo(card, { opacity: 0, y: 24, scale: 0.94 }, { opacity: 1, y: 0, scale: 1, ease: "power3.out", duration: 0.6 }, 0.55)
        .fromTo(feed, { opacity: 0, y: 6 }, { opacity: 1, y: 0, stagger: 0.12, duration: 0.3 }, ">-0.1");
      return () => setAnimated(false);
    });
    return () => mm.revert();
  }, []);

  return (
    <section id="impact" className="rq-impact" aria-labelledby="impact-h">
      <div className="container">
        <div className="rq-head">
          <h2 id="impact-h" className="rq-h2">The repeat work stops repeating.</h2>
          <p className="rq-intro">Every team has a task someone does by hand, the same way, every week. Show it once and it’s off their plate.</p>
        </div>

        <div ref={pin} className="rq-fold" data-animated={animated || undefined}>
          <div className="rq-week" role="img" aria-label="A week of urgent cases escalated by hand, about 15 a week at 4 to 12 minutes each, recedes behind one approved task that runs on its own.">
            {WEEK.map((d) => (
              <div key={d.day} className="rq-week-col" aria-hidden="true">
                <p className="rq-week-day">{d.day}</p>
                {d.tiles.map(([t, time, took, who]) => (
                  <div key={t} className="rq-tile">
                    <p className="rq-tile-k">Escalated by hand · took {took}</p>
                    <p className="rq-tile-t">{t}</p>
                    <p className="rq-tile-m"><span className="num">{time}</span><span className="rq-tile-av">{who}</span></p>
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="rq-fold-card">
            <div className="rq-machined">
              <div className="rq-machined-top">
                <p className="rq-machined-t">Escalate urgent cases</p>
                <p className="rq-machined-pill">Tool</p>
              </div>
              <p className="rq-machined-r">{ruleText(answer)}</p>
              <Lifecycle state="Tool" />
              <ul className="rq-feed" aria-label="Recent runs">
                {FEED.map(([time, what]) => (
                  <li key={time}><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg><span className="num">Ran {time}</span>{what}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        <ul className="rq-claims">
          <li data-tone="human"><b>Written down once.</b> The recording becomes the instructions, so nobody re-explains the task.</li>
          <li data-tone="cobalt"><b>Checked by a person.</b> Nothing runs until someone on your team approves the plan.</li>
          <li data-tone="emerald"><b>Run to your rule.</b> The agent follows the answers you gave, and stops for review when something unexpected happens.</li>
        </ul>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- connect */

function AppIcon({ name }: { name: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const paths: Record<string, React.ReactNode> = {
    CRM: <><circle cx="9" cy="8" r="3" {...p} /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" {...p} /><path d="M16 5.5a3 3 0 0 1 0 5.5M18 19a5 5 0 0 0-2.5-4.3" {...p} /></>,
    "Help desk": <><path d="M4 13v-1a8 8 0 0 1 16 0v1" {...p} /><rect x="3" y="13" width="4" height="6" rx="1.5" {...p} /><rect x="17" y="13" width="4" height="6" rx="1.5" {...p} /></>,
    Spreadsheet: <><rect x="3.5" y="4.5" width="17" height="15" rx="2" {...p} /><path d="M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15" {...p} /></>,
    "Admin panel": <><path d="M5 6h14M5 12h14M5 18h14" {...p} /><circle cx="9" cy="6" r="1.8" {...p} /><circle cx="15" cy="12" r="1.8" {...p} /><circle cx="8" cy="18" r="1.8" {...p} /></>,
    Billing: <><path d="M6 3.5h12v17l-3-2-3 2-3-2-3 2z" {...p} /><path d="M9 8.5h6M9 12h6" {...p} /></>,
    Inbox: <><path d="M3.5 13.5 6 5h12l2.5 8.5v5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z" {...p} /><path d="M3.5 13.5H9l1 2h4l1-2h5.5" {...p} /></>,
    Claude: <><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4" {...p} /></>,
    Cursor: <><path d="M5 3l14 9-6 1.5L10 20z" {...p} /></>,
    "Your own agents": <><rect x="5" y="7" width="14" height="11" rx="3" {...p} /><path d="M12 3v4M9 12h.01M15 12h.01" {...p} /></>,
    "Other MCP tools": <><path d="M9 7V4M15 7V4M7 7h10v5a5 5 0 0 1-10 0zM12 17v4" {...p} /></>,
  };
  return <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">{paths[name]}</svg>;
}

const APPS = ["CRM", "Help desk", "Spreadsheet", "Admin panel", "Billing", "Inbox"];
const AGENTS = [
  { name: "Claude", note: "uses your workflows" },
  { name: "Cursor", note: "uses your workflows" },
  { name: "Your own agents", note: "use your workflows" },
  { name: "Other MCP tools", note: "Repliq calls them" },
];
// Geometry (SVG units = px of the 420px-tall flow): hub sits at y 100–320.
const HUB_TOP = 100, HUB_H = 220;
const LEFT_PORTS = [0.2, 0.32, 0.44, 0.56, 0.68, 0.8];
const RIGHT_PORTS = [0.2, 0.4, 0.6, 0.8];

function Connect() {
  const { approved } = useStory();
  const ly = APPS.map((_, i) => 35 + i * 70);
  const ry = AGENTS.map((_, i) => 52 + i * 105);
  const lp = LEFT_PORTS.map((p) => HUB_TOP + p * HUB_H);
  const rp = RIGHT_PORTS.map((p) => HUB_TOP + p * HUB_H);
  const left = ly.map((y, i) => `M280 ${y} C 345 ${y}, 335 ${lp[i]}, 400 ${lp[i]}`);
  const right = ry.map((y, i) => `M600 ${rp[i]} C 665 ${rp[i]}, 655 ${y}, 720 ${y}`);
  return (
    <section id="connect" className="rq-connect" aria-labelledby="connect-h">
      <div className="container">
        <div className="rq-head">
          <h2 id="connect-h" className="rq-h2">Any app in. Any agent out.</h2>
          <p className="rq-intro">Every task you approve becomes a tool. Your team can run it, and so can your other AI agents.</p>
        </div>

        <div className="rq-flow">
          <svg className="rq-flow-wires" viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
            {[...left, ...right].map((d, i) => <path key={`b${i}`} className="rq-trace-base" d={d} />)}
            {left.map((d, i) => <path key={`l${i}`} className="rq-trace-beam" d={d} style={{ animationDelay: `${-i * 0.35}s` }} />)}
            {right.map((d, i) => (
              <path key={`r${i}`} className={i === 3 ? "rq-trace-beam is-back" : "rq-trace-beam"} d={d} style={{ animationDelay: `${0.9 - i * 0.35}s` }} />
            ))}
          </svg>

          <div className="rq-flow-col rq-flow-apps">
            <p className="rq-flow-h">Apps your team signs in to</p>
            <ul>{APPS.map((a) => <li key={a}><span className="rq-node"><span>{a}</span><AppIcon name={a} /></span></li>)}</ul>
          </div>

          <div className="rq-flow-mid">
            <div className="rq-tool">
              {LEFT_PORTS.map((p) => <i key={`lp${p}`} className="rq-port" style={{ left: -4, top: `${p * 100}%` }} />)}
              {RIGHT_PORTS.map((p) => <i key={`rp${p}`} className="rq-port" style={{ right: -4, top: `${p * 100}%` }} />)}
              <p className="rq-tool-badge">Now a tool</p>
              <p className="rq-tool-t">Escalate urgent cases</p>
              <p className="rq-tool-m">{approved ? "Approved by you" : "Approved by your team"}</p>
            </div>
          </div>

          <div className="rq-flow-col rq-flow-agents">
            <p className="rq-flow-h">Agents and tools, over MCP</p>
            <ul>{AGENTS.map((a) => (
              <li key={a.name}><span className="rq-node rq-node--agent" data-back={a.name === "Other MCP tools" || undefined}><AppIcon name={a.name} /><span><b>{a.name}</b><em>{a.note}</em></span></span></li>
            ))}</ul>
          </div>
        </div>

        <div className="rq-flow-notes">
          <p><b>Your agents can use it.</b> Repliq works as an MCP server, so assistants like Claude, Cursor or your own agents can run the workflows you’ve approved.</p>
          <p><b>It can use your tools.</b> Repliq connects to other MCP tools too, so one workflow can reach the systems your agents already use.</p>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ how it works */

const STAGES = [
  { t: "Your recording comes in", d: "Repliq transcribes what you said and keeps the frames where the screen changed." },
  { t: "A plan is drafted", d: "What you said and what you showed become steps anyone can read." },
  { t: "You approve it", d: "Your answers become rules. Nothing runs until someone approves." },
  { t: "An agent runs it", d: "In a fresh, isolated browser it grounds every element on the live screen, decides, acts and looks again. It never just replays clicks.", note: "Your sign-ins stay in Repliq’s vault. Each run gets a one-time pass." },
  { t: "It checks the result", d: "Afterwards it confirms the records really changed, and that nothing else did." },
];

function Stage({ i, active, onActive, onJump, children }: {
  i: number; active: number; onActive: (i: number) => void; onJump: (i: number) => void; children: React.ReactNode;
}) {
  const ref = useRef<HTMLLIElement>(null);
  const inView = useInView(ref, { margin: "-45% 0px -45% 0px" });
  useEffect(() => { if (inView) onActive(i); }, [inView, i, onActive]);
  return (
    <li ref={ref} id={`stage-${i}`} className="rq-stage" data-on={active === i || undefined} data-done={active > i || undefined} data-long={i === 3 || undefined}>
      <button type="button" className="rq-stage-btn" onClick={() => onJump(i)} aria-current={active === i ? "step" : undefined}>
        <span className="rq-stage-n num">{i + 1}</span>
        <span className="rq-h3">{STAGES[i].t}</span>
      </button>
      <div className="rq-stage-body">{children}</div>
    </li>
  );
}

function HowItWorks() {
  const [active, setActive] = useState(0);
  const [tick, setTick] = useState(0);
  const reduce = useReducedMotion();
  const runRef = useRef<HTMLElement | null>(null);
  // The run stage drives the browser frame by frame as it scrolls through.
  useLayoutEffect(() => { runRef.current = document.getElementById("stage-3"); }, []);
  const { scrollYProgress } = useScroll({ target: runRef, offset: ["start 0.6", "end 0.55"] });
  useMotionValueEvent(scrollYProgress, "change", (p) => setTick(Math.round(p * RUN_TICKS)));

  const jump = useCallback((i: number) => {
    const el = document.getElementById(`stage-${i}`);
    if (!el) return;
    const y = el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.4;
    window.scrollTo({ top: y, behavior: reduce ? "auto" : "smooth" });
  }, [reduce]);

  return (
    <section id="how" className="rq-how" aria-labelledby="how-h">
      <div className="container">
        <div className="rq-head">
          <h2 id="how-h" className="rq-h2">Look, decide, act, check.</h2>
          <p className="rq-intro">What happens between your recording and a finished job, in one sandbox browser.</p>
        </div>
        <div className="rq-how-grid">
          <div className="rq-how-sticky"><AgentRun stage={active} tick={reduce ? RUN_TICKS : tick} /></div>
          <div className="rq-stages" style={{ "--progress": active / (STAGES.length - 1) } as React.CSSProperties}>
            <span className="rq-rail" aria-hidden="true"><span className="rq-rail-fill" /></span>
            <ol>
            {STAGES.map((s, i) => (
              <Stage key={s.t} i={i} active={active} onActive={setActive} onJump={jump}>
                <p>{s.d}</p>
                {s.note && <p className="rq-stage-note">{s.note}</p>}
              </Stage>
            ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}

/* --------------------------------------------------- white paper + footer */

function PaperCallout() {
  return (
    <section className="rq-paper" aria-labelledby="paper-h">
      <div className="container">
        <div className="rq-paper-card">
          <div className="rq-paper-stack" aria-hidden="true">
            <span className="rq-sheet rq-sheet--3" />
            <span className="rq-sheet rq-sheet--2" />
            <span className="rq-sheet rq-sheet--1">
              <b>Demonstration-Guided Agentic Automation</b>
              <i /><i /><i /><i /><i />
              <em>POC / Architecture Paper</em>
            </span>
          </div>
          <div className="rq-paper-copy">
            <ul className="rq-paper-tags">
              <li>Technical paper</li>
              <li>Multimodal grounding &amp; episodic memory</li>
              <li>PDF · 9 pages · 12 min read</li>
            </ul>
            <h2 id="paper-h" className="rq-paper-t">Demonstration-Guided Agentic Automation</h2>
            <p className="rq-paper-d">Turning screen recordings into executable business processes: how Repliq interprets a narrated demonstration, plans, runs against the live UI, and verifies the outcome.</p>
            <a href={PAPER_URL} className="rq-paper-btn">
              Read whitepaper (PDF)
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8" /></svg>
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="rq-footer" data-ambient>
      <div className="rq-aurora" aria-hidden="true">
        <span className="rq-aurora-band rq-aurora-band--cobalt" />
        <span className="rq-aurora-band rq-aurora-band--emerald" />
      </div>
      <div className="rq-aurora-ribs" aria-hidden="true" />
      <div className="container rq-footer-cta">
        <h2 className="rq-h2">Bring the task your team repeats most.</h2>
        <div className="rq-actions">
          <Link href="/login" className="rq-btn rq-btn--onDark">Get started</Link>
          <a href={DEMO_URL} className="rq-link rq-link--onDark" target="_blank" rel="noreferrer">Watch the demo</a>
        </div>
      </div>
      <div className="container rq-footer-inner">
        <p className="rq-footer-note">Agent automation for the work your team repeats.</p>
        <ul className="rq-footer-links">
          <li><a href={DEMO_URL} target="_blank" rel="noreferrer">Demo video</a></li>
          <li><a href={PAPER_URL}>White paper</a></li>
          <li><Link href="/login">Sign in</Link></li>
        </ul>
        <div className="rq-footer-meta">
          <p className="rq-footer-copy">© 2026 Repliq</p>
          <AmbientMotion />
        </div>
      </div>
      <div className="container rq-footer-markwrap">
        <p className="rq-footer-mark">Repliq</p>
      </div>
    </footer>
  );
}

export function Below() {
  return (
    <>
      <main id="main">
        <WhatWeDo />
        <Impact />
        <Connect />
        <HowItWorks />
        <PaperCallout />
      </main>
      <Footer />
    </>
  );
}
