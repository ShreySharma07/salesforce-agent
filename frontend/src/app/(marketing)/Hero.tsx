// app/(marketing)/Hero.tsx
// Fluted-glass hero (Paper Shaders, WebGL). Behind the reeds: an app screen
// where someone is working. The glass relaxes as you scroll, so the work
// becomes readable, the way narrating a task makes it a plan. Only this
// section uses glass.
"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { motion, useMotionValueEvent, useReducedMotion, useScroll } from "framer-motion";
import { useRef, useState } from "react";
import { PlanReview } from "./PlanReview";

const FlutedGlass = dynamic(() => import("@paper-design/shaders-react").then((m) => m.FlutedGlass), {
  ssr: false,
  loading: () => <div className="rq-glass-fallback" />,
});

const DEMO_URL = "https://youtu.be/gEhxQjRQ6XI";

export function Hero() {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();
  const [distortion, setDistortion] = useState(0.5);

  // Scroll relaxes the reeds: distortion eases from 0.5 to 0.08.
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  useMotionValueEvent(scrollYProgress, "change", (p) => {
    if (reduce) return;
    const next = Math.round((0.5 - Math.min(p / 0.6, 1) * 0.42) * 50) / 50;
    setDistortion((d) => (d === next ? d : next));
  });

  return (
    <header ref={ref} className="rq-hero">
      <div className="rq-glass" aria-hidden="true">
        <FlutedGlass
          width="100%"
          height="100%"
          image="/hero-app.webp"
          colorBack="#00000000"
          colorShadow="#000000"
          colorHighlight="#ffffff"
          size={0.92}
          shadows={0.25}
          highlights={0.1}
          shape="lines"
          angle={0}
          distortionShape="prism"
          distortion={reduce ? 0.3 : distortion}
          shift={0}
          stretch={0}
          blur={0}
          edges={0.25}
          margin={0}
          grainMixer={0}
          grainOverlay={0}
          fit="cover"
          maxPixelCount={1920 * 1080}
        />
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
        <motion.div className="rq-hero-copy" initial={reduce ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}>
          <h1 className="rq-h1">Show it once. Now it’s a tool.</h1>
          <p className="rq-lead">Record a task in any web app your team uses. Approve the plan, and Repliq’s agents run it.</p>
          <div className="rq-actions rq-actions--hero">
            <Link href="/login" className="rq-btn">Get started</Link>
            <a href={DEMO_URL} className="rq-btn rq-btn--quiet" target="_blank" rel="noreferrer">Watch the demo</a>
          </div>
        </motion.div>
        <motion.div className="rq-hero-product" initial={reduce ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, delay: 0.12, ease: [0.16, 1, 0.3, 1] }}>
          <PlanReview />
        </motion.div>
      </div>
    </header>
  );
}
