// app/(marketing)/Hero.tsx
// Fluted-glass hero. Behind the glass: a generic app screen where someone is
// working (rows, a status pill, a cursor). The reeds refract it so the work is
// there but unreadable; scrolling clears the glass, the way narrating a task
// makes it readable. Only this section uses glass.
"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "framer-motion";
import { PlanReview } from "./PlanReview";

const DEMO_URL = "https://youtu.be/gEhxQjRQ6XI";
const FLUTE = 34; // px width of one reed

// A horizontal ramp in the red channel (green held at mid-grey) tiled across
// the hero: each reed shifts what is behind it by a different amount.
const RAMP = `data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='${FLUTE}' height='64'><defs><linearGradient id='g'><stop offset='0' stop-color='rgb(0,128,0)'/><stop offset='0.82' stop-color='rgb(255,128,0)'/><stop offset='1' stop-color='rgb(0,128,0)'/></linearGradient></defs><rect width='${FLUTE}' height='64' fill='url(#g)'/></svg>`,
)}`;

const ROWS = [
  ["Login loop after password reset", "Open", "Medium"],
  ["Invoice 4471 does not match PO", "Open", "Low"],
  ["Urgent: renewal blocked, $285K deal", "Escalated", "High"],
  ["Export to spreadsheet times out", "Pending", "Medium"],
  ["Add seats for the EMEA team", "Open", "Low"],
  ["Shipping address keeps resetting", "Open", "Medium"],
  ["Refund request over limit", "Pending", "High"],
];

function AppScreen({ active }: { active: number }) {
  return (
    <div className="rq-app" aria-hidden="true">
      <div className="rq-app-side">
        <span className="rq-app-logo" />
        {["Inbox", "Cases", "Accounts", "Tasks", "Reports"].map((l, i) => (
          <span key={l} className={i === 1 ? "rq-app-nav is-on" : "rq-app-nav"}>{l}</span>
        ))}
      </div>
      <div className="rq-app-main">
        <div className="rq-app-bar"><span>Cases</span><span className="rq-app-search">Search cases</span><span className="rq-app-btn">New case</span></div>
        <div className="rq-app-table">
          {ROWS.map(([t, s, p], i) => (
            <div key={t} className={i === active ? "rq-app-row is-active" : "rq-app-row"}>
              <span className="rq-app-check" />
              <span className="rq-app-title">{t}</span>
              <span className={`rq-app-pill rq-app-pill--${s.toLowerCase()}`}>{s}</span>
              <span className="rq-app-pri">{p}</span>
            </div>
          ))}
        </div>
      </div>
      <span className="rq-app-cursor" style={{ transform: `translate(var(--cx), calc(${active} * var(--row-h) + var(--cy)))` }} />
    </div>
  );
}

export function Hero() {
  const ref = useRef<HTMLElement>(null);
  const dispRef = useRef<SVGFEDisplacementMapElement>(null);
  const blurRef = useRef<SVGFEGaussianBlurElement>(null);
  const reduce = useReducedMotion();
  const [active, setActive] = useState(2);

  // The work behind the glass keeps going: the cursor moves row to row.
  useEffect(() => {
    if (reduce) return;
    const order = [0, 2, 3, 2, 6, 2];
    let k = 0;
    const id = window.setInterval(() => {
      if (document.querySelector(".rq")?.hasAttribute("data-motion-stopped")) return;
      k = (k + 1) % order.length;
      setActive(order[k]);
    }, 2600);
    return () => window.clearInterval(id);
  }, [reduce]);

  // Scrolling clears the glass: refraction and blur fall to zero.
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  const clear = useTransform(scrollYProgress, [0, 0.55], [1, 0]);
  const ribs = useTransform(clear, (v) => 0.25 + v * 0.75);
  useMotionValueEvent(clear, "change", (v) => {
    dispRef.current?.setAttribute("scale", String(Math.round(30 * v)));
    blurRef.current?.setAttribute("stdDeviation", (2.2 * v).toFixed(2));
  });

  // The cursor catches light along the reeds.
  function onMove(e: React.PointerEvent<HTMLElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
  }

  return (
    <header ref={ref} className="rq-hero" onPointerMove={onMove}>
      <svg className="rq-defs" aria-hidden="true" focusable="false">
        <filter id="rq-flute" x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
          <feImage href={RAMP} x="0" y="0" width={FLUTE} height="64" result="ramp" preserveAspectRatio="none" />
          <feTile in="ramp" result="reeds" />
          <feGaussianBlur ref={blurRef} in="SourceGraphic" stdDeviation="2.2" result="soft" />
          <feDisplacementMap ref={dispRef} in="soft" in2="reeds" scale="30" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </svg>

      <div className="rq-glass" aria-hidden="true">
        <div className="rq-glass-behind"><AppScreen active={active} /></div>
        <motion.div className="rq-glass-ribs" style={{ opacity: ribs }} />
        <div className="rq-glass-light" />
        <div className="rq-glass-veil" />
      </div>

      <nav className="rq-nav container" aria-label="Main">
        <Link href="/" className="rq-wordmark">Repliq</Link>
        <ul className="rq-nav-links">
          <li><a href="#what">What it does</a></li>
          <li><a href="#impact">Impact</a></li>
          <li><a href="#connect">Connect</a></li>
          <li><a href="#how">How it works</a></li>
        </ul>
        <div className="rq-nav-actions">
          <Link href="/login" className="rq-nav-signin">Sign in</Link>
          <Link href="/login" className="rq-btn rq-btn--small">Get started</Link>
        </div>
      </nav>

      <div className="rq-hero-grid container">
        <motion.div className="rq-hero-copy" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}>
          <h1 className="rq-h1">Show it once. Now it’s a tool.</h1>
          <p className="rq-lead">Record a task in any web app your team uses. Approve the plan, and Repliq’s agents run it for you.</p>
          <div className="rq-actions">
            <Link href="/login" className="rq-btn">Get started</Link>
            <a href={DEMO_URL} className="rq-btn rq-btn--quiet" target="_blank" rel="noreferrer">Watch the 60‑second demo</a>
          </div>
        </motion.div>
        <motion.div className="rq-hero-product" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, delay: 0.12, ease: [0.16, 1, 0.3, 1] }}>
          <PlanReview />
        </motion.div>
      </div>
    </header>
  );
}
