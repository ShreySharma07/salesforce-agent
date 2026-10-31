// app/(marketing)/page.tsx
// Repliq landing. White paper, a slanted light-blue field drawn as a tilted
// editing timeline (the recording), and the product itself as the hero.
import type { Metadata } from "next";
import Link from "next/link";
import "./landing.css";
import { PlanReview } from "./PlanReview";
import { AmbientMotion } from "./AmbientMotion";

export const metadata: Metadata = {
  title: "Repliq: show a Salesforce task once, then hand it over",
  description:
    "Record a Salesforce task, approve the plan Repliq writes, and an agent repeats it through the live UI.",
};

const DEMO_URL = "https://youtu.be/gEhxQjRQ6XI";
const PAPER_URL = "/repliq-white-paper.pdf";

/* A deterministic narration waveform, periodic over `period` so it can loop
   seamlessly as the timeline drifts. */
const TAU = Math.PI * 2;
function waveform(period: number, copies: number, mid: number, amp: number) {
  const pts: string[] = [];
  for (let x = 0; x <= period * copies; x += 8) {
    const t = x / period;
    const y = Math.sin(TAU * 77 * t) * 0.5 + Math.sin(TAU * 241 * t) * 0.3 + Math.sin(TAU * 29 * t) * 0.6;
    const h = Math.abs(y) * amp * (0.45 + 0.55 * Math.abs(Math.sin(TAU * 6 * t)));
    pts.push(`M${x} ${(mid - h).toFixed(1)}V${(mid + h).toFixed(1)}`);
  }
  return pts.join("");
}

function HeroField() {
  const frames = Array.from({ length: 44 }, (_, i) => i * 44);
  return (
    <div className="rq-field" aria-hidden="true" data-ambient>
      <svg viewBox="0 0 1760 640" preserveAspectRatio="none" className="rq-field-svg">
        <rect className="f-base" x="0" y="0" width="1760" height="640" />
        <rect className="f-track-a" x="0" y="404" width="1760" height="72" />
        <g className="f-wave-run"><path className="f-wave" d={waveform(1760, 2, 440, 26)} /></g>
        <rect className="f-track-b" x="0" y="476" width="1760" height="100" />
        <g className="f-frame-run">
          {frames.map((x) => (
            <rect key={x} className="f-frame" x={x + 6} y="490" width="34" height="72" rx="3" />
          ))}
        </g>
        <rect className="f-track-c" x="0" y="576" width="1760" height="64" />
        <rect className="f-head" x="1268" y="392" width="3" height="248" />
      </svg>
    </div>
  );
}

const TIMELINE = [
  { at: "0:31", said: "This one’s on a $285K deal, it goes first.", showed: "Case open, linked $285K opportunity", step: "Handle cases on open deals first" },
  { at: "0:52", said: "Bump it to High and escalate.", showed: "Priority High, Escalated checked", step: "Set priority to High and escalate" },
  { at: "1:18", said: "Then a follow-up for the owner.", showed: "New task, due today", step: "Create a follow-up task due today" },
];

const TRACE = [
  { kind: "Look", text: "Case list loaded, newest first" },
  { kind: "Decide", text: "Urgent case links to an open $285K deal" },
  { kind: "Act", text: "Set priority to High, check Escalated" },
  { kind: "Look", text: "Escalate moved; found it in the menu" },
];

const CHECKS = [
  "Priority on the urgent case reads High",
  "Exactly one follow-up task exists",
  "Cases that didn’t qualify are unchanged",
];

export default function Landing() {
  return (
    <>
      <a href="#main" className="rq-skip">Skip to content</a>

      <header className="rq-hero">
        <HeroField />
        <nav className="rq-nav container" aria-label="Main">
          <Link href="/" className="rq-wordmark">Repliq</Link>
          <ul className="rq-nav-links">
            <li><a href="#how">How it works</a></li>
            <li><a href="#review">Review</a></li>
            <li><a href="#run">The run</a></li>
            <li><a href="#security">Security</a></li>
          </ul>
          <div className="rq-nav-actions">
            <Link href="/login" className="rq-nav-signin">Sign in</Link>
            <Link href="/login" className="rq-btn rq-btn--small">Get started</Link>
          </div>
        </nav>

        <div className="rq-hero-grid container">
          <div className="rq-hero-copy">
            <h1 className="rq-h1">Show Repliq once, then hand it over</h1>
            <p className="rq-lead">Record a Salesforce task, approve the plan, and an agent repeats it.</p>
            <div className="rq-actions">
              <Link href="/login" className="rq-btn">Get started</Link>
              <a href={DEMO_URL} className="rq-btn rq-btn--quiet" target="_blank" rel="noreferrer">Watch the 60‑second demo</a>
            </div>
          </div>
          <div className="rq-hero-product">
            <PlanReview />
          </div>
        </div>
      </header>

      <main id="main">
        <section id="how" className="rq-section container" aria-labelledby="how-h">
          <div className="rq-head">
            <h2 id="how-h" className="rq-h2">Your recording becomes a readable plan</h2>
            <p className="rq-intro">Repliq lines up what you said with what the screen showed.</p>
          </div>

          <div className="rq-sheet">
            <table className="rq-table">
              <caption className="visually-hidden">How narration and screen frames become plan steps</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col"><span className="rq-key rq-key--you" aria-hidden="true" />You said</th>
                  <th scope="col">On screen</th>
                  <th scope="col"><span className="rq-key rq-key--agent" aria-hidden="true" />Plan step</th>
                </tr>
              </thead>
              <tbody>
                {TIMELINE.map((r) => (
                  <tr key={r.at}>
                    <td className="num rq-td-time" data-label="Time">{r.at}</td>
                    <td data-label="You said" className="rq-td-said">“{r.said}”</td>
                    <td data-label="On screen" className="rq-td-showed">{r.showed}</td>
                    <td data-label="Plan step" className="rq-td-step">{r.step}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section id="review" className="rq-band" aria-labelledby="review-h">
          <div className="rq-band-inner container rq-split">
            <div className="rq-split-copy">
              <h2 id="review-h" className="rq-h2">Nothing runs until you approve it</h2>
              <p className="rq-intro">One example isn’t a policy. Say what a step got wrong.</p>
            </div>

            <figure className="rq-correction">
              <figcaption className="rq-correction-cap"><span className="rq-key rq-key--you" aria-hidden="true" />Your correction</figcaption>
              <blockquote className="rq-note">
                <p>Only if the deal is still open. Closed‑lost deals wait.</p>
              </blockquote>
              <p className="rq-diff-label">Step 1, updated</p>
              <div className="rq-diff">
                <p className="rq-diff-row rq-diff-row--old"><span className="visually-hidden">Removed: </span>Handle cases on any deal first</p>
                <p className="rq-diff-row rq-diff-row--new"><span className="visually-hidden">Added: </span>Handle cases on open deals first</p>
              </div>
            </figure>
          </div>
        </section>

        <section id="run" className="rq-section container" aria-labelledby="run-h">
          <div className="rq-head">
            <h2 id="run-h" className="rq-h2">It reads the screen every step</h2>
            <p className="rq-intro">No replayed clicks. It looks, acts, then checks the result.</p>
          </div>

          <div className="rq-run">
            <ol className="rq-trace" aria-label="Agent reasoning trace">
              {TRACE.map((t, i) => (
                <li key={i} className="rq-trace-row">
                  <span className="rq-trace-kind">{t.kind}</span>
                  <span className="rq-trace-text">{t.text}</span>
                </li>
              ))}
            </ol>
            <div className="rq-verify">
              <h3 className="rq-h3">Checked in Salesforce after the run</h3>
              <ul className="rq-checks">
                {CHECKS.map((c) => (
                  <li key={c}>
                    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="rq-check-icon"><path d="M20 6 9 17l-5-5" /></svg>
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section id="security" className="rq-night" aria-labelledby="sec-h">
          <div className="rq-night-inner container">
            <div className="rq-head">
              <h2 id="sec-h" className="rq-h2">Your password never reaches the browser</h2>
              <p className="rq-intro">Each run gets a fresh, isolated browser. The agent acts as you, inside your Salesforce permissions.</p>
            </div>

            <div className="rq-arch" role="img" aria-label="Architecture: the backend holds the credential vault, Salesforce sign-in, model access and the plan. Only a per-run token crosses the boundary to a fresh sandbox, where the agent drives Chromium against the live Salesforce UI.">
              <div className="rq-arch-node rq-arch-node--input">
                <h3 className="rq-arch-title">Your recording</h3>
                <p className="rq-arch-sub">Screen and narration</p>
              </div>

              <div className="rq-arch-node rq-arch-node--backend">
                <h3 className="rq-arch-title">Backend</h3>
                <p className="rq-arch-sub">Holds every secret</p>
                <ul className="rq-arch-list">
                  <li>Credential vault</li>
                  <li>Salesforce sign‑in (OAuth)</li>
                  <li>Model access</li>
                  <li>Recording to plan</li>
                  <li>Run history</li>
                </ul>
              </div>

              <div className="rq-arch-boundary">
                <span className="rq-arch-line" />
                <p className="rq-arch-token">Only a per‑run token crosses</p>
              </div>

              <div className="rq-arch-node rq-arch-node--sandbox">
                <h3 className="rq-arch-title">Sandbox</h3>
                <p className="rq-arch-sub">New for every run</p>
                <ul className="rq-arch-list">
                  <li>Approved plan</li>
                  <li>Look, decide, act loop</li>
                  <li>Chromium</li>
                </ul>
              </div>

              <div className="rq-arch-node rq-arch-node--target">
                <h3 className="rq-arch-title">Salesforce</h3>
                <p className="rq-arch-sub">The live UI, as you</p>
              </div>
            </div>
          </div>
        </section>

        <section className="rq-close container" aria-labelledby="close-h">
          <h2 id="close-h" className="rq-h2 rq-close-h">Bring your most repetitive task</h2>
          <p className="rq-intro">Record it once. You’ll see the plan before anything runs.</p>
          <div className="rq-actions">
            <Link href="/login" className="rq-btn">Get started</Link>
            <a href={PAPER_URL} className="rq-link">Read the white paper (PDF)</a>
          </div>
        </section>
      </main>

      <footer className="rq-footer" data-ambient>
        <div className="rq-aurora" aria-hidden="true">
          <span className="rq-aurora-band rq-aurora-band--steel" />
          <span className="rq-aurora-band rq-aurora-band--purple" />
          <span className="rq-aurora-band rq-aurora-band--blue" />
          <span className="rq-aurora-band rq-aurora-band--pink" />
        </div>
        <div className="container rq-footer-inner">
          <p className="rq-footer-note">A proof of concept for demonstration‑guided automation.</p>
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
    </>
  );
}
