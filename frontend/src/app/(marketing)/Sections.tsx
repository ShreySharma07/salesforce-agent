// app/(marketing)/Sections.tsx
// Everything below the hero. No glass: solid layers telling one story, with
// one task card advancing its state as you go: draft (hero) → approved (what
// it does) → tool (impact, connect) → running → verified (how it works).
"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, useInView } from "framer-motion";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { ruleText, useStory } from "./story";
import { AmbientMotion } from "./AmbientMotion";
import { AgentRun } from "./AgentRun";

const VIDEO_ID = "gEhxQjRQ6XI";
const DEMO_URL = `https://youtu.be/${VIDEO_ID}`;
const PAPER_URL = "/repliq-white-paper.pdf";

/* ------------------------------------------------------- the task's states */

const LIFECYCLE = ["Draft", "Approved", "Tool", "Running", "Verified"] as const;
type Life = (typeof LIFECYCLE)[number];

function StatusCard({ state, note }: { state: Life; note?: string }) {
  const { answer } = useStory();
  const at = LIFECYCLE.indexOf(state);
  return (
    <div className="rq-status" data-state={state.toLowerCase()}>
      <div className="rq-status-top">
        <p className="rq-status-t">Escalate urgent cases</p>
        <p className="rq-status-pill">{state}</p>
      </div>
      <p className="rq-status-r">{ruleText(answer)}</p>
      <ol className="rq-life" aria-label={`Task status: ${state}`}>
        {LIFECYCLE.map((l, i) => (
          <li key={l} data-on={i <= at || undefined} data-now={i === at || undefined}>{l}</li>
        ))}
      </ol>
      {note && <p className="rq-status-note">{note}</p>}
    </div>
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
            <span className="rq-video-play" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M8 5.5v13l10.5-6.5z" /></svg></span>
            <span className="rq-video-len">Demo · 60 seconds</span>
          </button>
        )}
      </div>
      <figcaption className="rq-caption">A support case on a $285K deal: recorded once, then run by the agent.</figcaption>
    </figure>
  );
}

function WhatWeDo() {
  const { answer } = useStory();
  return (
    <section id="what" className="rq-what" aria-labelledby="what-h">
      <div className="container rq-what-grid">
        <div className="rq-what-copy">
          <h2 id="what-h" className="rq-h2 rq-verbs">
            <span>Say it.</span><span>Show it.</span><span>Approve it.</span>
          </h2>
          <ol className="rq-beats">
            <li><b>Say it</b> Narrate the task as you work, like you’d explain it to a new hire.</li>
            <li><b>Show it</b> Repliq lines up what you said with what you clicked.</li>
            <li><b>Approve it</b> You get a plan in plain words; anything unclear becomes a question.</li>
          </ol>
        </div>
        <div className="rq-what-media">
          <DemoVideo />
          <StatusCard
            state={answer === null ? "Draft" : "Approved"}
            note={answer === null ? "Answer the question in the plan above to approve it." : "Your answer is now part of the plan."}
          />
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ impact */

const WEEK: { day: string; tiles: [string, string, string][] }[] = [
  { day: "Mon", tiles: [["Renewal blocked, $285K", "9:12", "PL"], ["SSO outage, 40 seats", "11:40", "MR"], ["Refund over limit", "16:05", "AJ"]] },
  { day: "Tue", tiles: [["Login loop, enterprise", "8:47", "PL"], ["Data export failing", "13:20", "SK"], ["Invoice disputed", "15:55", "MR"]] },
  { day: "Wed", tiles: [["Renewal at risk", "10:02", "AJ"], ["API keys revoked", "12:31", "PL"], ["Seat limit reached", "17:10", "SK"]] },
  { day: "Thu", tiles: [["Contract stuck in legal", "9:30", "MR"], ["Payment failed twice", "14:15", "AJ"], ["Outage reported", "16:48", "PL"]] },
  { day: "Fri", tiles: [["Renewal blocked again", "8:55", "SK"], ["Admin locked out", "11:05", "MR"], ["Billing mismatch", "15:30", "AJ"]] },
];

function Impact() {
  const pin = useRef<HTMLDivElement>(null);
  const [animated, setAnimated] = useState(false);

  useLayoutEffect(() => {
    gsap.registerPlugin(ScrollTrigger);
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const root = pin.current;
      if (!root) return;
      setAnimated(true);
      const card = root.querySelector<HTMLElement>(".rq-fold-card")!;
      const tiles = gsap.utils.toArray<HTMLElement>(".rq-tile", root);
      const delta = (el: HTMLElement) => {
        const a = el.getBoundingClientRect(), c = card.getBoundingClientRect();
        return { x: c.left + c.width / 2 - (a.left + a.width / 2), y: c.top + c.height / 2 - (a.top + a.height / 2) };
      };
      const tl = gsap.timeline({
        scrollTrigger: { trigger: root, start: "top 18%", end: "+=110%", pin: true, scrub: 0.6, invalidateOnRefresh: true },
      });
      tl.to(tiles, {
        x: (_i: number, el: HTMLElement) => delta(el).x,
        y: (_i: number, el: HTMLElement) => delta(el).y,
        scale: 0.5, opacity: 0, ease: "power2.in", stagger: { each: 0.03, from: "edges" }, duration: 1,
      }).fromTo(card, { opacity: 0, scale: 0.86 }, { opacity: 1, scale: 1, ease: "power2.out", duration: 0.5 }, ">-0.35");
      return () => { setAnimated(false); };
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
          <div className="rq-week" role="img" aria-label="A week of urgent cases escalated by hand, Monday to Friday, folds into one approved task that runs on its own.">
            {WEEK.map((d) => (
              <div key={d.day} className="rq-week-col" aria-hidden="true">
                <p className="rq-week-day">{d.day}</p>
                {d.tiles.map(([t, time, who]) => (
                  <div key={t} className="rq-tile">
                    <p className="rq-tile-k">Escalate by hand</p>
                    <p className="rq-tile-t">{t}</p>
                    <p className="rq-tile-m"><span className="num">{time}</span><span className="rq-tile-av">{who}</span></p>
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="rq-fold-card">
            <StatusCard state="Tool" note="Approved once. Runs on its own, every time a case qualifies." />
          </div>
        </div>

        <ul className="rq-claims">
          <li data-tone="human">Written down once.</li>
          <li data-tone="cobalt">Checked by a person.</li>
          <li data-tone="emerald">Run to the rule you approved.</li>
        </ul>
        <p className="rq-impact-eg">Works for the chores every team has: escalating tickets, matching invoices, updating records, setting up new accounts.</p>
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
  };
  return <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">{paths[name]}</svg>;
}

const APPS = ["CRM", "Help desk", "Spreadsheet", "Admin panel", "Billing", "Inbox"];
const AGENTS = [
  { name: "Claude", note: "uses your workflows" },
  { name: "Cursor", note: "uses your workflows" },
  { name: "Your own agents", note: "use your workflows" },
  { name: "Other MCP tools", note: "Repliq can call them" },
];

function Connect() {
  const { approved } = useStory();
  const L = APPS.map((_, i) => 35 + i * 70);
  const R = AGENTS.map((_, i) => 52 + i * 105);
  return (
    <section id="connect" className="rq-connect" aria-labelledby="connect-h">
      <div className="container">
        <div className="rq-head">
          <h2 id="connect-h" className="rq-h2">Any app in. Any agent out.</h2>
          <p className="rq-intro">Every task you approve becomes a tool. Your team can run it, and so can your other AI agents.</p>
        </div>

        <div className="rq-flow">
          <svg className="rq-flow-wires" viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
            {L.map((y, i) => <path key={`l${i}`} className="rq-wire-in" d={`M280 ${y} C 340 ${y}, 340 210, 400 210`} />)}
            {R.map((y, i) => (
              <g key={`r${i}`}>
                <path className="rq-wire-out" d={`M600 ${204} C 660 204, 660 ${y - 6}, 720 ${y - 6}`} />
                <path className="rq-wire-back" d={`M720 ${y + 6} C 660 ${y + 6}, 660 216, 600 216`} />
              </g>
            ))}
          </svg>

          <div className="rq-flow-col rq-flow-apps">
            <p className="rq-flow-h">Apps your team signs in to</p>
            <ul>{APPS.map((a) => <li key={a}><AppIcon name={a} />{a}</li>)}</ul>
          </div>

          <div className="rq-flow-mid">
            <div className="rq-tool">
              <p className="rq-tool-badge">Now a tool</p>
              <p className="rq-tool-t">Escalate urgent cases</p>
              <p className="rq-tool-m">{approved ? "Approved by you, just now" : "Approved by a person on your team"}</p>
            </div>
          </div>

          <div className="rq-flow-col rq-flow-agents">
            <p className="rq-flow-h">Agents and tools, over MCP</p>
            <ul>{AGENTS.map((a) => <li key={a.name}><b>{a.name}</b><span>{a.note}</span></li>)}</ul>
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
  { t: "You approve it", d: "Anything Repliq can’t tell becomes a question. Nothing runs until you say so." },
  { t: "An agent runs it", d: "In a fresh, isolated browser it grounds every element on the live screen, decides, acts and looks again. It never just replays clicks.", note: "Your sign-ins stay in Repliq’s vault. Each run gets a one-time pass." },
  { t: "It checks the result", d: "Afterwards it confirms the records really changed, and that nothing else did." },
];

function Stage({ i, active, onActive, children }: { i: number; active: number; onActive: (i: number) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLLIElement>(null);
  const inView = useInView(ref, { margin: "-45% 0px -45% 0px" });
  useEffect(() => { if (inView) onActive(i); }, [inView, i, onActive]);
  return <li ref={ref} className="rq-stage" data-on={active === i || undefined} data-done={active > i || undefined}>{children}</li>;
}

function HowItWorks() {
  const [active, setActive] = useState(0);
  return (
    <section id="how" className="rq-how" aria-labelledby="how-h">
      <div className="container">
        <div className="rq-head">
          <h2 id="how-h" className="rq-h2">Look, decide, act, check.</h2>
          <p className="rq-intro">What happens between your recording and a finished job, in one sandbox browser.</p>
        </div>
        <div className="rq-how-grid">
          <div className="rq-how-sticky"><AgentRun stage={active} /></div>
          <ol className="rq-stages">
            {STAGES.map((s, i) => (
              <Stage key={s.t} i={i} active={active} onActive={setActive}>
                <span className="rq-stage-n num">{i + 1}</span>
                <div className="rq-stage-body">
                  <h3 className="rq-h3">{s.t}</h3>
                  <p>{s.d}</p>
                  {s.note && <p className="rq-stage-note">{s.note}</p>}
                </div>
              </Stage>
            ))}
          </ol>
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
        <motion.a href={PAPER_URL} className="rq-paper-card" whileHover={{ y: -2 }} transition={{ duration: 0.2 }}>
          <span className="rq-paper-doc" aria-hidden="true"><i /><i /><i /><i /></span>
          <span className="rq-paper-copy">
            <span id="paper-h" className="rq-paper-t">Read the white paper</span>
            <span className="rq-paper-d">How Repliq turns a narrated recording into a plan, runs it against the live UI, and verifies the outcome.</span>
          </span>
          <span className="rq-paper-cta">Open PDF<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8" /></svg></span>
        </motion.a>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="rq-footer" data-ambient>
      <div className="rq-aurora" aria-hidden="true">
        <span className="rq-aurora-band rq-aurora-band--stone" />
        <span className="rq-aurora-band rq-aurora-band--cobalt" />
        <span className="rq-aurora-band rq-aurora-band--emerald" />
        <span className="rq-aurora-band rq-aurora-band--amber" />
      </div>
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
