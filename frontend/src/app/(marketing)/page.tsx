// app/(marketing)/page.tsx
// Stripe-style landing, converted from the Claude Design export. Uses a real
// Three.js hero canvas, scroll-reveal hook, and Next routing. All CTAs point
// at /login (your working auth).
"use client";

import Link from "next/link";
import { HeroCanvas } from "@/components/HeroCanvas";
import { useScrollReveal } from "@/components/useScrollReveal";

const ARR = "M5 12h14M13 6l6 6-6 6";
const CHECK = "M20 6 9 17l-5-5";

function Arrow({ c = "#fff", s = 14 }: { c?: string; s?: number }) {
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c}
      strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
      <path d={ARR} />
    </svg>
  );
}

const FEATURES = [
  { t: "Learns by watching", d: "Record yourself doing the task once. No scripting, no selectors, no brittle macros to maintain." },
  { t: "Genuinely agentic", d: "A Reason → Act → Observe loop reasons over live screenshots and adapts. It never replays fixed clicks." },
  { t: "Zero-trust by design", d: "The sandbox that touches the web never holds a credential or API key. Ever." },
  { t: "Watchable & auditable", d: "Watch every run live in your browser. Each run keeps a full step-by-step reasoning trace." },
  { t: "Connected apps, on demand", d: "The agent decides when it needs Salesforce and logs in itself — via a one-time token, never a password." },
];

const STEPS = [
  { n: 1, t: "Record", d: "Capture yourself doing the task once on screen.", c: "#6366f1" },
  { n: 2, t: "Plan", d: "FFmpeg keyframes → vision-LLM captions → an executable plan.", c: "#6d5ae6" },
  { n: 3, t: "Run", d: "A fresh Docker sandbox spawns and executes autonomously.", c: "#7e57e0" },
  { n: 4, t: "Watch", d: "Follow the live browser view (noVNC) as it works.", c: "#5aa3d8" },
  { n: 5, t: "Inspect", d: "Review every thought, action, and observation — plus cost.", c: "#1aa6bd" },
];

export default function Landing() {
  const root = useScrollReveal<HTMLDivElement>();

  return (
    <div ref={root} style={{ position: "relative", overflowX: "hidden", background: "#fff" }}>
      {/* NAV */}
      <nav style={{ position: "sticky", top: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 32px", background: "rgba(255,255,255,0.72)", backdropFilter: "blur(18px)", WebkitBackdropFilter: "blur(18px)", borderBottom: "1px solid rgba(11,18,51,0.06)" }}>
        <a href="#top" style={{ display: "flex", alignItems: "center", gap: 11, textDecoration: "none" }}>
          <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 32, height: 32, borderRadius: 9, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", boxShadow: "0 6px 16px rgba(99,102,241,0.4)" }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#fff" }} />
          </span>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.03em", color: "#0b1233" }}>Repliq</span>
        </a>
        <div style={{ display: "flex", alignItems: "center", gap: 32 }}>
          {[["Product", "#features"], ["How it works", "#how"], ["Security", "#security"], ["Architecture", "#architecture"]].map(([l, h]) => (
            <a key={l} href={h} style={{ fontSize: 15, fontWeight: 500, color: "#42496b", textDecoration: "none" }}>{l}</a>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <Link href="/login" style={{ fontSize: 15, fontWeight: 600, color: "#42496b", textDecoration: "none" }}>Sign in</Link>
          <Link href="/login" style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 15, fontWeight: 600, color: "#fff", textDecoration: "none", padding: "10px 18px", borderRadius: 999, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", boxShadow: "0 6px 18px rgba(99,102,241,0.38)" }}>
            Get started <Arrow />
          </Link>
        </div>
      </nav>

      {/* HERO */}
      <section id="top" style={{ position: "relative", minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "80px 24px 100px", background: "linear-gradient(180deg,#ffffff 0%,#f5f4ff 55%,#edefff 100%)", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: "6%", left: "8%", width: 480, height: 480, borderRadius: "50%", background: "radial-gradient(circle,rgba(139,92,246,0.42),rgba(139,92,246,0) 70%)", filter: "blur(20px)", animation: "floatBlob 11s ease-in-out infinite", pointerEvents: "none" }} />
        <div style={{ position: "absolute", bottom: "2%", right: "6%", width: 520, height: 520, borderRadius: "50%", background: "radial-gradient(circle,rgba(34,211,238,0.34),rgba(34,211,238,0) 70%)", filter: "blur(20px)", animation: "floatBlob 13s ease-in-out infinite reverse", pointerEvents: "none" }} />
        <HeroCanvas />

        {/* LEFT glass card */}
        <div data-anim style={{ position: "absolute", left: 24, bottom: 44, width: 262, zIndex: 6, padding: 18, borderRadius: 18, background: "rgba(255,255,255,0.45)", backdropFilter: "blur(22px)", WebkitBackdropFilter: "blur(22px)", border: "1px solid rgba(255,255,255,0.7)", boxShadow: "0 24px 60px rgba(40,30,90,0.18)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", animation: "blink 1.6s ease-in-out infinite" }} />
            <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: "#6b73a0" }}>Reasoning trace · live</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace", fontSize: 12.5 }}>
            <div style={{ color: "#8b5cf6" }}><span style={{ color: "#9aa1c4" }}>think</span> &nbsp;locate &quot;New Lead&quot; button</div>
            <div style={{ color: "#6366f1" }}><span style={{ color: "#9aa1c4" }}>act</span> &nbsp;&nbsp;&nbsp;&nbsp;click (412, 230)</div>
            <div style={{ color: "#0891b2" }}><span style={{ color: "#9aa1c4" }}>observe</span> form opened ✓</div>
          </div>
        </div>

        {/* RIGHT glass card */}
        <div data-anim style={{ position: "absolute", right: 24, bottom: 44, width: 258, zIndex: 6, padding: 18, borderRadius: 18, background: "rgba(255,255,255,0.45)", backdropFilter: "blur(22px)", WebkitBackdropFilter: "blur(22px)", border: "1px solid rgba(255,255,255,0.7)", boxShadow: "0 24px 60px rgba(40,30,90,0.18)" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: "#0b1233" }}>Sandbox #4821</span>
            <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 9px", borderRadius: 999, background: "rgba(34,197,94,0.14)", color: "#16a34a" }}>running</span>
          </div>
          <div style={{ height: 84, borderRadius: 11, background: "linear-gradient(135deg,#1e1b4b,#312e81)", position: "relative", overflow: "hidden", marginBottom: 12 }}>
            <div style={{ position: "absolute", top: 9, left: 10, display: "flex", gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#f87171" }} />
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#fbbf24" }} />
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#34d399" }} />
            </div>
            <div style={{ position: "absolute", bottom: 12, left: 10, right: 10, height: 8, borderRadius: 4, background: "rgba(255,255,255,0.16)" }} />
            <div style={{ position: "absolute", bottom: 12, left: 10, width: "62%", height: 8, borderRadius: 4, background: "linear-gradient(90deg,#a78bfa,#22d3ee)" }} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5, color: "#6b73a0" }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6366f1" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
            holds only a per-run token
          </div>
        </div>

        {/* hero copy */}
        <div style={{ position: "relative", zIndex: 5, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", maxWidth: 860 }}>
          <span data-anim style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "7px 16px", borderRadius: 999, background: "rgba(99,102,241,0.10)", border: "1px solid rgba(99,102,241,0.18)", fontSize: 13, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: "#5b54e6", marginBottom: 28 }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#8b5cf6" }} />Autonomous web-task agents
          </span>
          <h1 data-anim style={{ fontSize: 76, lineHeight: 1.02, fontWeight: 700, letterSpacing: "-0.045em", color: "#0b1233", marginBottom: 26 }}>
            Watch a task once.<br />
            <span style={{ background: "linear-gradient(120deg,#6366f1,#8b5cf6 55%,#22d3ee)", WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>The agent runs it forever.</span>
          </h1>
          <p data-anim style={{ fontSize: 21, lineHeight: 1.55, color: "#4a5578", maxWidth: 620, marginBottom: 38 }}>
            Repliq turns a single screen recording into a repeatable automation. It writes an executable plan, runs it in an isolated cloud sandbox, and reasons its way through any web UI — the way a person would.
          </p>
          <div data-anim style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", justifyContent: "center" }}>
            <Link href="/login" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 17, fontWeight: 600, color: "#fff", textDecoration: "none", padding: "16px 30px", borderRadius: 999, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", boxShadow: "0 12px 34px rgba(99,102,241,0.42)" }}>
              Get started <Arrow s={16} />
            </Link>
            <a href="#architecture" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 17, fontWeight: 600, color: "#0b1233", textDecoration: "none", padding: "16px 28px", borderRadius: 999, background: "rgba(255,255,255,0.7)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", border: "1px solid rgba(11,18,51,0.10)" }}>
              See the architecture <Arrow c="#0b1233" s={15} />
            </a>
          </div>
          <div data-anim style={{ display: "flex", alignItems: "center", gap: 22, marginTop: 34, fontSize: 13.5, color: "#7c84a8", flexWrap: "wrap", justifyContent: "center" }}>
            {["No scripting or selectors", "Zero-trust sandbox", "Full reasoning trace"].map((t) => (
              <span key={t} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"><path d={CHECK} /></svg>{t}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* TRUST STRIP */}
      <section style={{ padding: "34px 24px", background: "#fff", borderBottom: "1px solid rgba(11,18,51,0.06)" }}>
        <p style={{ textAlign: "center", fontSize: 13, fontWeight: 600, letterSpacing: "0.1em", textTransform: "uppercase", color: "#9aa1bd", marginBottom: 22 }}>First focus: Salesforce data hygiene — general by design</p>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 48, flexWrap: "wrap", opacity: 0.62 }}>
          {["Leads", "Contacts", "Records", "Any web UI"].map((t) => (
            <span key={t} style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em", color: "#3a4166" }}>{t}</span>
          ))}
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" style={{ padding: "130px 24px", background: "linear-gradient(180deg,#ffffff,#f7f7ff)" }}>
        <div style={{ maxWidth: 1180, margin: "0 auto" }}>
          <div data-reveal style={{ maxWidth: 720, marginBottom: 64 }}>
            <span style={{ display: "inline-block", padding: "7px 16px", borderRadius: 999, background: "rgba(99,102,241,0.10)", fontSize: 13, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "#5b54e6", marginBottom: 22 }}>What makes it different</span>
            <h2 style={{ fontSize: 52, lineHeight: 1.05, fontWeight: 700, letterSpacing: "-0.04em", color: "#0b1233", marginBottom: 20 }}>Not a macro. A genuine agent.</h2>
            <p style={{ fontSize: 20, lineHeight: 1.55, color: "#4a5578" }}>Brittle scripts break the moment a button moves. Repliq reasons over what it sees and adapts in real time — so it keeps working when the page changes.</p>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 22 }}>
            {FEATURES.map((f, i) => (
              <div key={f.t} data-reveal id={i === 2 ? "security" : undefined} style={{ padding: 30, borderRadius: 22, background: "rgba(255,255,255,0.6)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", border: "1px solid rgba(255,255,255,0.8)", boxShadow: "0 18px 44px rgba(40,30,90,0.08)" }}>
                <div style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 50, height: 50, borderRadius: 14, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", marginBottom: 20, boxShadow: "0 8px 20px rgba(99,102,241,0.35)" }}>
                  <span style={{ width: 18, height: 18, borderRadius: "50%", border: "2px solid #fff" }} />
                </div>
                <h3 style={{ fontSize: 21, fontWeight: 700, letterSpacing: "-0.02em", color: "#0b1233", marginBottom: 10 }}>{f.t}</h3>
                <p style={{ fontSize: 15.5, lineHeight: 1.6, color: "#5a6488" }}>{f.d}</p>
              </div>
            ))}
            <div data-reveal style={{ padding: 30, borderRadius: 22, background: "linear-gradient(135deg,#312e81,#4338ca)", boxShadow: "0 22px 50px rgba(49,46,129,0.4)", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
              <div>
                <h3 style={{ fontSize: 21, fontWeight: 700, letterSpacing: "-0.02em", color: "#fff", marginBottom: 10 }}>General, not Salesforce-specific.</h3>
                <p style={{ fontSize: 15.5, lineHeight: 1.6, color: "#c7c9ff" }}>Data hygiene is just the first focus. Nothing in the architecture is tied to one app — it&apos;s a general web-task agent.</p>
              </div>
              <Link href="/login" style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 24, fontSize: 15, fontWeight: 600, color: "#fff", textDecoration: "none" }}>See it run <Arrow /></Link>
            </div>
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how" style={{ padding: "130px 24px", background: "#fff" }}>
        <div style={{ maxWidth: 1180, margin: "0 auto" }}>
          <div data-reveal style={{ textAlign: "center", maxWidth: 720, margin: "0 auto 70px" }}>
            <span style={{ display: "inline-block", padding: "7px 16px", borderRadius: 999, background: "rgba(99,102,241,0.10)", fontSize: 13, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "#5b54e6", marginBottom: 22 }}>How it works</span>
            <h2 style={{ fontSize: 52, lineHeight: 1.05, fontWeight: 700, letterSpacing: "-0.04em", color: "#0b1233", marginBottom: 18 }}>From recording to results in five steps.</h2>
            <p style={{ fontSize: 20, lineHeight: 1.55, color: "#4a5578" }}>Record once. Repliq handles the rest — plan, run, watch, inspect.</p>
          </div>
          <div style={{ position: "relative", display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 18 }}>
            <div style={{ position: "absolute", top: 26, left: "9%", right: "9%", height: 2, background: "linear-gradient(90deg,#6366f1,#8b5cf6,#22d3ee)", opacity: 0.4 }} />
            {STEPS.map((s) => (
              <div key={s.n} data-reveal style={{ position: "relative", textAlign: "center" }}>
                <div style={{ width: 54, height: 54, margin: "0 auto 22px", borderRadius: "50%", background: "#fff", border: `2px solid ${s.c}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 19, fontWeight: 700, color: s.c, boxShadow: "0 6px 16px rgba(99,102,241,0.18)" }}>{s.n}</div>
                <h4 style={{ fontSize: 17, fontWeight: 700, letterSpacing: "0.02em", textTransform: "uppercase", color: "#0b1233", marginBottom: 9 }}>{s.t}</h4>
                <p style={{ fontSize: 14.5, lineHeight: 1.55, color: "#5a6488" }}>{s.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ARCHITECTURE */}
      <section id="architecture" style={{ padding: "130px 24px", background: "linear-gradient(180deg,#0b1233,#1a1450)", color: "#fff" }}>
        <div style={{ maxWidth: 1000, margin: "0 auto" }}>
          <div data-reveal style={{ textAlign: "center", maxWidth: 760, margin: "0 auto 64px" }}>
            <span style={{ display: "inline-block", padding: "7px 16px", borderRadius: 999, background: "rgba(139,92,246,0.18)", fontSize: 13, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "#c4b5fd", marginBottom: 22 }}>Architecture</span>
            <h2 style={{ fontSize: 52, lineHeight: 1.05, fontWeight: 700, letterSpacing: "-0.04em", color: "#fff", marginBottom: 18 }}>Two processes. One hard security boundary.</h2>
            <p style={{ fontSize: 20, lineHeight: 1.55, color: "#b9bee0" }}>The half that touches the web never holds a secret. Ever.</p>
          </div>

          <div data-reveal style={{ padding: 30, borderRadius: 22, background: "rgba(255,255,255,0.05)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", border: "1px solid rgba(255,255,255,0.12)", boxShadow: "0 24px 60px rgba(0,0,0,0.3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 40, height: 40, borderRadius: 11, background: "linear-gradient(135deg,#6366f1,#8b5cf6)", fontSize: 20 }}>🧠</span>
              <div>
                <div style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.01em" }}>Backend</div>
                <div style={{ fontSize: 13, color: "#a78bfa", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase" }}>Holds ALL secrets</div>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
              {["API layer", "Credential vault", "Video → plan", "Memory system", "OAuth + frontdoor", "LLM proxy", "Sandbox runner"].map((b) => (
                <div key={b} style={{ padding: "13px 14px", borderRadius: 11, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>{b}</div>
              ))}
              <div style={{ padding: "13px 14px", borderRadius: 11, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 14, fontWeight: 600, color: "#e6e8ff", gridColumn: "span 2" }}>Database (SQLite / Postgres)</div>
            </div>
          </div>

          <div data-reveal style={{ display: "flex", alignItems: "center", gap: 18, padding: "18px 6px" }}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <svg width="20" height="34" viewBox="0 0 20 34" fill="none" stroke="#8b5cf6" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M10 2v26M4 22l6 6 6-6" /></svg>
              <span style={{ fontSize: 12.5, color: "#b9bee0", textAlign: "center" }}>spawns + sends Plan<br /><span style={{ color: "#8089b5" }}>scoped per-run token</span></span>
            </div>
            <div style={{ flex: "0 0 auto", padding: "8px 18px", borderRadius: 999, background: "rgba(139,92,246,0.16)", border: "1px dashed rgba(167,139,250,0.5)", fontSize: 12, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "#c4b5fd" }}>Hard security boundary</div>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <svg width="20" height="34" viewBox="0 0 20 34" fill="none" stroke="#22d3ee" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M10 32V6M4 12l6-6 6 6" /></svg>
              <span style={{ fontSize: 12.5, color: "#b9bee0", textAlign: "center" }}>LLM &amp; tool calls<br /><span style={{ color: "#8089b5" }}>no secrets travel down</span></span>
            </div>
          </div>

          <div data-reveal style={{ padding: 30, borderRadius: 22, background: "rgba(34,211,238,0.06)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", border: "1px solid rgba(34,211,238,0.22)", boxShadow: "0 24px 60px rgba(0,0,0,0.3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 40, height: 40, borderRadius: 11, background: "linear-gradient(135deg,#0891b2,#22d3ee)", fontSize: 20 }}>🦾</span>
              <div>
                <div style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.01em" }}>Sandbox</div>
                <div style={{ fontSize: 13, color: "#22d3ee", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase" }}>Docker, one per run</div>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 18px", borderRadius: 12, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)" }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>Executor</span>
                <Arrow c="#22d3ee" s={16} />
                <span style={{ fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>ReAct loop</span>
                <Arrow c="#22d3ee" s={16} />
                <span style={{ fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>Chromium</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "14px 18px", borderRadius: 12, background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.3)", fontSize: 13.5, fontWeight: 600, color: "#86efac" }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#86efac" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
                Holds ONLY a per-run token
              </div>
            </div>
          </div>

          <div data-reveal style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 16, marginTop: 26 }}>
            <span style={{ fontSize: 13, color: "#8089b5" }}>Reaches out to</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "9px 16px", borderRadius: 999, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)", fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>🌐 Gemini</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "9px 16px", borderRadius: 999, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)", fontSize: 14, fontWeight: 600, color: "#e6e8ff" }}>☁️ Salesforce</span>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section id="cta" style={{ position: "relative", padding: "140px 24px", background: "linear-gradient(135deg,#4f46e5,#7c3aed 55%,#0891b2)", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: "-20%", left: "-5%", width: 520, height: 520, borderRadius: "50%", background: "radial-gradient(circle,rgba(255,255,255,0.18),rgba(255,255,255,0) 70%)", filter: "blur(10px)", pointerEvents: "none" }} />
        <div style={{ position: "absolute", bottom: "-25%", right: "-5%", width: 560, height: 560, borderRadius: "50%", background: "radial-gradient(circle,rgba(34,211,238,0.4),rgba(34,211,238,0) 70%)", filter: "blur(20px)", pointerEvents: "none" }} />
        <div data-reveal style={{ position: "relative", zIndex: 2, maxWidth: 760, margin: "0 auto", textAlign: "center" }}>
          <h2 style={{ fontSize: 60, lineHeight: 1.04, fontWeight: 700, letterSpacing: "-0.045em", color: "#fff", marginBottom: 22 }}>Watch a task once.<br />It runs forever.</h2>
          <p style={{ fontSize: 21, lineHeight: 1.55, color: "rgba(255,255,255,0.85)", marginBottom: 40 }}>See Repliq turn a screen recording into an autonomous agent that reasons its way through any web UI — live, in a secure sandbox.</p>
          <div style={{ display: "flex", alignItems: "center", gap: 16, justifyContent: "center", flexWrap: "wrap" }}>
            <Link href="/login" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 17, fontWeight: 600, color: "#4338ca", textDecoration: "none", padding: "16px 32px", borderRadius: 999, background: "#fff", boxShadow: "0 14px 36px rgba(0,0,0,0.22)" }}>Get started</Link>
            <a href="#architecture" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 17, fontWeight: 600, color: "#fff", textDecoration: "none", padding: "16px 30px", borderRadius: 999, background: "rgba(255,255,255,0.14)", border: "1px solid rgba(255,255,255,0.4)", backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)" }}>See the architecture</a>
          </div>
        </div>
      </section>

      {/* FOOTER */}
      <footer style={{ padding: "70px 32px 40px", background: "#0b1233", color: "#fff" }}>
        <div style={{ maxWidth: 1180, margin: "0 auto", display: "flex", justifyContent: "space-between", gap: 48, flexWrap: "wrap", paddingBottom: 48, borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
          <div style={{ maxWidth: 300 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 16 }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 30, height: 30, borderRadius: 9, background: "linear-gradient(135deg,#6366f1,#8b5cf6)" }}><span style={{ width: 9, height: 9, borderRadius: "50%", background: "#fff" }} /></span>
              <span style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.03em" }}>Repliq</span>
            </div>
            <p style={{ fontSize: 14.5, lineHeight: 1.6, color: "#9aa1c4" }}>Watch a task once. The agent does it forever — autonomously, in a secure sandbox.</p>
          </div>
          <div style={{ display: "flex", gap: 64, flexWrap: "wrap" }}>
            {[["Product", ["Features", "How it works", "Architecture", "Security"]], ["Company", ["About", "Careers", "Contact"]], ["Resources", ["Docs", "CLI", "Changelog"]]].map(([h, items]) => (
              <div key={h as string} style={{ display: "flex", flexDirection: "column", gap: 13 }}>
                <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "#6b73a0", marginBottom: 2 }}>{h as string}</span>
                {(items as string[]).map((it) => (
                  <a key={it} href="#" style={{ fontSize: 14.5, color: "#c2c7e0", textDecoration: "none" }}>{it}</a>
                ))}
              </div>
            ))}
          </div>
        </div>
        <div style={{ maxWidth: 1180, margin: "24px auto 0", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 18, flexWrap: "wrap", fontSize: 13.5, color: "#6b73a0" }}>
          <span>© 2026 Repliq. All rights reserved.</span>
          <span>Privacy · Terms · Security</span>
        </div>
      </footer>
    </div>
  );
}