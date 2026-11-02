// app/(marketing)/Sections.tsx
// Everything below the hero. No glass here: solid layers that tell one story.
// What it does (the person's view) → Impact → Connect → How it works (the
// agent's view) → footer. The visitor's hero answer echoes through each.
"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, useInView, useScroll, useTransform, type MotionValue } from "framer-motion";
import { ruleText, useStory } from "./story";
import { AmbientMotion } from "./AmbientMotion";

const VIDEO_ID = "gEhxQjRQ6XI";
const DEMO_URL = `https://youtu.be/${VIDEO_ID}`;
const PAPER_URL = "/repliq-white-paper.pdf";
const EASE = [0.16, 1, 0.3, 1] as const;

const reveal = {
  initial: { opacity: 0, y: 18 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, amount: 0.4 },
  transition: { duration: 0.7, ease: EASE },
};

/* ------------------------------------------------------------ what it does */

function DemoVideo() {
  const [playing, setPlaying] = useState(false);
  const [thumbOk, setThumbOk] = useState(true);
  const imgRef = useRef<HTMLImageElement>(null);
  // The thumbnail can fail before hydration attaches onError; check once mounted.
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
            <span className="rq-video-len">Demo, 60 seconds</span>
          </button>
        )}
      </div>
      <figcaption className="rq-caption">Demo: a support case on a $285K deal, recorded once, then run by the agent.</figcaption>
    </figure>
  );
}

const BEATS = [
  { t: "Say it", d: "Narrate the task while you do it, the way you’d explain it to a new hire." },
  { t: "Show it", d: "Repliq watches the screen and lines up what you said with what you clicked." },
  { t: "Approve it", d: "You get a plan in plain words. Anything unclear becomes a question for you." },
];

function WhatWeDo() {
  const { answer } = useStory();
  return (
    <section id="what" className="rq-what" aria-labelledby="what-h">
      <div className="container">
        <motion.div className="rq-head" {...reveal}>
          <h2 id="what-h" className="rq-h2">Say it, show it, approve it.</h2>
          <p className="rq-intro">No scripts and no flowcharts. You teach Repliq the way you’d teach a colleague: by doing the work once.</p>
        </motion.div>

        <div className="rq-what-grid">
          <motion.div {...reveal}><DemoVideo /></motion.div>
          <motion.ol className="rq-beats" {...reveal} transition={{ ...reveal.transition, delay: 0.1 }}>
            {BEATS.map((b, i) => (
              <li key={b.t} className="rq-beat">
                <span className="rq-beat-n num">{i + 1}</span>
                <div>
                  <h3 className="rq-h3">{b.t}</h3>
                  <p>{b.d}</p>
                  {i === 2 && (
                    <p className="rq-echo" data-set={answer !== null}>
                      {answer === null
                        ? "Try it: answer the question in the plan above."
                        : <>Your answer is now part of the plan: <strong>{ruleText(answer)}</strong>.</>}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </motion.ol>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ impact */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
const PER_DAY = 3;

function WeekCard({ col, row, p }: { col: number; row: number; p: MotionValue<number> }) {
  const delay = (col * PER_DAY + row) / (DAYS.length * PER_DAY) * 0.35;
  const x = useTransform(p, [delay, delay + 0.45], ["0%", `${(2 - col) * 108}%`]);
  const y = useTransform(p, [delay, delay + 0.45], ["0%", `${(1 - row) * 118}%`]);
  const o = useTransform(p, [delay + 0.25, delay + 0.5], [1, 0]);
  return (
    <motion.div className="rq-week-card" style={{ x, y, opacity: o }}>
      <span className="rq-week-dot" />Escalate urgent case
    </motion.div>
  );
}

function Impact() {
  const { answer } = useStory();
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.5", "end 0.25"] });
  const oneO = useTransform(scrollYProgress, [0.55, 0.85], [0, 1]);
  const oneS = useTransform(scrollYProgress, [0.55, 0.85], [0.9, 1]);

  return (
    <section id="impact" className="rq-impact" aria-labelledby="impact-h">
      <div className="container">
        <motion.div className="rq-head" {...reveal}>
          <h2 id="impact-h" className="rq-h2">The repeat work stops repeating.</h2>
          <p className="rq-intro">Every team has a task someone does by hand, the same way, every week. Show it once and it’s off their plate.</p>
        </motion.div>

        <div ref={ref} className="rq-week" role="img" aria-label="A week of the same task done by hand, Monday to Friday, folds into one approved task that runs on its own.">
          <div className="rq-week-days" aria-hidden="true">{DAYS.map((d) => <span key={d}>{d}</span>)}</div>
          <div className="rq-week-grid" aria-hidden="true">
            {DAYS.map((d, c) => (
              <div key={d} className="rq-week-col">
                {Array.from({ length: PER_DAY }, (_, r) => <WeekCard key={r} col={c} row={r} p={scrollYProgress} />)}
              </div>
            ))}
          </div>
          <motion.div className="rq-week-one" style={{ opacity: oneO, scale: oneS }} aria-hidden="true">
            <p className="rq-week-one-t">Escalate urgent cases</p>
            <p className="rq-week-one-r">{ruleText(answer)}</p>
            <p className="rq-week-one-s">Approved once. Runs on its own.</p>
          </motion.div>
        </div>

        <ul className="rq-claims">
          {["Written down once.", "Checked by a person.", "Run to the rule you approved."].map((c, i) => (
            <motion.li key={c} {...reveal} transition={{ ...reveal.transition, delay: i * 0.08 }}>{c}</motion.li>
          ))}
        </ul>
        <p className="rq-impact-eg">Works for the chores every team has: escalating tickets, matching invoices, updating records, setting up new accounts.</p>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- connect */

const APPS = [
  { name: "CRM", shape: "rows" },
  { name: "Help desk", shape: "thread" },
  { name: "Spreadsheet", shape: "grid" },
  { name: "Admin panel", shape: "form" },
  { name: "Billing", shape: "bars" },
  { name: "Inbox", shape: "list" },
];

function Connect() {
  const { answer, approved } = useStory();
  return (
    <section id="connect" className="rq-connect" aria-labelledby="connect-h">
      <div className="container">
        <motion.div className="rq-head rq-head--center" {...reveal}>
          <h2 id="connect-h" className="rq-h2">Any app in. Any agent out.</h2>
          <p className="rq-intro">Every task you approve becomes a tool: your team can run it, and so can your other AI agents.</p>
        </motion.div>

        <div className="rq-hub">
          <motion.ul className="rq-apps" {...reveal}>
            {APPS.map((a) => (
              <li key={a.name} className="rq-app-chip">
                <span className={`rq-glyph rq-glyph--${a.shape}`} aria-hidden="true"><i /><i /><i /></span>
                {a.name}
              </li>
            ))}
          </motion.ul>
          <p className="rq-hub-note">If your team signs in to it in a browser, Repliq can learn it.</p>

          <div className="rq-wire" aria-hidden="true" />

          <motion.div
            className="rq-tool"
            initial={{ opacity: 0, rotateX: 18, y: 24, scale: 0.94 }}
            whileInView={{ opacity: 1, rotateX: 0, y: 0, scale: 1 }}
            viewport={{ once: true, amount: 0.6 }}
            transition={{ duration: 0.9, ease: EASE }}
          >
            <p className="rq-tool-badge">Now a tool</p>
            <h3 className="rq-tool-t">Escalate urgent cases</h3>
            <p className="rq-tool-r">{ruleText(answer)}</p>
            <p className="rq-tool-m">{approved ? "Approved by you, just now" : "Approved by a person on your team"}</p>
          </motion.div>

          <div className="rq-wire rq-wire--split" aria-hidden="true" />

          <div className="rq-sockets">
            <motion.div className="rq-socket" {...reveal}>
              <h3 className="rq-h3">Your agents can use it</h3>
              <p>Repliq works as an MCP server, so assistants like Claude, Cursor or your own agents can run the workflows you’ve approved.</p>
            </motion.div>
            <motion.div className="rq-socket" {...reveal} transition={{ ...reveal.transition, delay: 0.1 }}>
              <h3 className="rq-h3">It can use your tools</h3>
              <p>Repliq can connect to other MCP tools too, so one workflow can reach the systems your agents already use.</p>
            </motion.div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ how it works */

function Screen({ stage }: { stage: number }) {
  const { answer } = useStory();
  return (
    <div className="rq-screen" data-stage={stage} aria-hidden="true">
      {stage === 0 && (
        <div className="rq-scr-rec">
          <div className="rq-scr-frames">{Array.from({ length: 5 }, (_, i) => <span key={i} className={i === 2 ? "is-key" : ""} />)}</div>
          <p className="rq-scr-line"><b>You said</b> “This one’s on a $285K deal, so it goes first.”</p>
          <p className="rq-scr-line"><b>On screen</b> Case open, linked opportunity at $285K</p>
        </div>
      )}
      {stage === 1 && (
        <ol className="rq-scr-plan">
          {["Open new cases in the queue", "Find the case on the open deal", "Set priority to High and escalate", "Create a follow-up task"].map((s, i) => (
            <li key={s}><span className="num">{i + 1}</span>{s}</li>
          ))}
        </ol>
      )}
      {stage === 2 && (
        <div className="rq-scr-ask">
          <p className="rq-scr-q">Should every urgent case get High priority?</p>
          <p className="rq-scr-a">{ruleText(answer)}</p>
          <p className="rq-scr-ok">Approved</p>
        </div>
      )}
      {stage === 3 && (
        <div className="rq-scr-run">
          {["Look: the case list loaded, newest first", "Decide: this case is on an open deal", "Act: set priority to High, escalate", "Look again: the button moved, found it"].map((t, i) => (
            <p key={t} style={{ "--i": i } as React.CSSProperties}>{t}</p>
          ))}
        </div>
      )}
      {stage === 4 && (
        <ul className="rq-scr-checks">
          {["Priority on the case reads High", "Exactly one follow-up task exists", "Cases that didn’t qualify are unchanged"].map((t) => (
            <li key={t}><svg viewBox="0 0 24 24" width="18" height="18"><path d="M20 6 9 17l-5-5" /></svg>{t}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const STAGES = [
  { t: "Your recording comes in", d: "Repliq transcribes what you said and keeps the frames where the screen changed." },
  { t: "A plan is drafted", d: "What you said and what you showed become steps anyone can read." },
  { t: "You approve it", d: "Anything Repliq can’t tell becomes a question. Nothing runs until you say so." },
  { t: "An agent runs it", d: "In a fresh, isolated browser it looks at the live screen, decides, acts and looks again. It never just replays clicks.", note: "Your sign-ins stay in Repliq’s vault. Each run gets a one-time pass." },
  { t: "It checks the result", d: "Afterwards it confirms the records really changed, and that nothing else did." },
];

function Stage({ i, onActive, children }: { i: number; onActive: (i: number) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLLIElement>(null);
  const inView = useInView(ref, { amount: 0.6 });
  useEffect(() => { if (inView) onActive(i); }, [inView, i, onActive]);
  return <li ref={ref} className="rq-stage">{children}</li>;
}

function HowItWorks() {
  const [active, setActive] = useState(0);
  return (
    <section id="how" className="rq-how" aria-labelledby="how-h">
      <div className="container">
        <motion.div className="rq-head" {...reveal}>
          <h2 id="how-h" className="rq-h2">Look, decide, act, check.</h2>
          <p className="rq-intro">What happens between your recording and a finished job.</p>
        </motion.div>
        <div className="rq-how-grid">
          <ol className="rq-stages">
            {STAGES.map((s, i) => (
              <Stage key={s.t} i={i} onActive={setActive}>
                <span className="rq-stage-n num" data-on={active >= i}>{i + 1}</span>
                <div className="rq-stage-body" data-on={active === i}>
                  <h3 className="rq-h3">{s.t}</h3>
                  <p>{s.d}</p>
                  {s.note && <p className="rq-stage-note">{s.note}</p>}
                  <div className="rq-stage-screen"><Screen stage={i} /></div>
                </div>
              </Stage>
            ))}
          </ol>
          <div className="rq-how-sticky">
            <motion.div key={active} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: EASE }}>
              <Screen stage={active} />
            </motion.div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ footer */

function Footer() {
  return (
    <footer className="rq-footer" data-ambient>
      <div className="rq-aurora" aria-hidden="true">
        <span className="rq-aurora-band rq-aurora-band--steel" />
        <span className="rq-aurora-band rq-aurora-band--purple" />
        <span className="rq-aurora-band rq-aurora-band--blue" />
        <span className="rq-aurora-band rq-aurora-band--pink" />
      </div>
      <div className="container rq-footer-cta">
        <h2 className="rq-h2">Bring the task your team repeats most.</h2>
        <div className="rq-actions">
          <Link href="/login" className="rq-btn rq-btn--onDark">Get started</Link>
          <a href={PAPER_URL} className="rq-link rq-link--onDark">Read the white paper (PDF)</a>
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
      </main>
      <Footer />
    </>
  );
}
