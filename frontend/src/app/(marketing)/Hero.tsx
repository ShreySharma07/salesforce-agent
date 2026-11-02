// app/(marketing)/Hero.tsx
// The opening: what Repliq is, in one screen. Fluted glass (Paper Shaders,
// WebGL) over an app where work is happening. The reeds follow the cursor and
// relax as you scroll, so the work behind becomes readable. Only this section
// uses fluted glass. A compact nav takes over once the hero scrolls away.
"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { AnimatePresence, motion, useMotionValueEvent, useReducedMotion, useScroll } from "framer-motion";

const FlutedGlass = dynamic(() => import("@paper-design/shaders-react").then((m) => m.FlutedGlass), {
  ssr: false,
  loading: () => <div className="rq-glass-fallback" />,
});

const DEMO_URL = "https://youtu.be/gEhxQjRQ6XI";
const LINKS = [
  ["what", "What it does"],
  ["impact", "Impact"],
  ["connect", "Connect"],
  ["how", "How it works"],
] as const;

function StickyNav({ show }: { show: boolean }) {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && setActive(e.target.id)),
      { rootMargin: "-45% 0px -50% 0px" },
    );
    LINKS.forEach(([id]) => { const el = document.getElementById(id); if (el) io.observe(el); });
    return () => io.disconnect();
  }, []);
  return (
    <AnimatePresence>
      {show && (
        <motion.nav
          className="rq-pill"
          data-section={active ?? undefined}
          aria-label="Sections"
          initial={{ y: -24, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -24, opacity: 0 }}
          transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        >
          <Link href="/" className="rq-pill-mark">Repliq</Link>
          <ul className="rq-pill-links">
            {LINKS.map(([id, label]) => (
              <li key={id}><a href={`#${id}`} aria-current={active === id ? "true" : undefined}>{label}</a></li>
            ))}
          </ul>
          <Link href="/login" className="rq-btn rq-btn--small">Get started</Link>
        </motion.nav>
      )}
    </AnimatePresence>
  );
}

export function Hero() {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();
  const [distortion, setDistortion] = useState(0.5);
  const [shift, setShift] = useState(0);
  const [pastHero, setPastHero] = useState(false);
  const [maxPx, setMaxPx] = useState(1920 * 1080);
  const target = useRef(0);
  const cur = useRef(0);
  const raf = useRef(0);

  // Phones render the shader at about half a megapixel.
  useEffect(() => {
    const set = () => setMaxPx(window.innerWidth < 768 ? 500_000 : 1920 * 1080);
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, []);

  // Scroll relaxes the reeds until the work behind is readable.
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  useMotionValueEvent(scrollYProgress, "change", (p) => {
    setPastHero(p > 0.92);
    if (reduce) return;
    const next = Math.round((0.5 - Math.min(p / 0.55, 1) * 0.48) * 50) / 50;
    setDistortion((d) => (d === next ? d : next));
  });

  // The reeds lean toward the cursor, eased.
  function onMove(e: React.PointerEvent<HTMLElement>) {
    if (reduce || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    target.current = ((e.clientX - r.left) / r.width - 0.5) * 0.5;
    if (raf.current) return;
    const step = () => {
      const n = cur.current + (target.current - cur.current) * 0.12;
      cur.current = Math.abs(target.current - n) < 0.002 ? target.current : n;
      setShift(Math.round(cur.current * 1000) / 1000);
      raf.current = cur.current === target.current ? 0 : requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
  }
  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  return (
    <>
      <StickyNav show={pastHero} />
      <header ref={ref} className="rq-hero" onPointerMove={onMove}>
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
            shift={shift}
            stretch={0}
            blur={0}
            edges={0.25}
            margin={0}
            grainMixer={0}
            grainOverlay={0}
            fit="cover"
            maxPixelCount={maxPx}
          />
          <div className="rq-glass-veil" />
        </div>

        <nav className="rq-nav container" aria-label="Main">
          <Link href="/" className="rq-wordmark">Repliq</Link>
          <ul className="rq-nav-links">
            {LINKS.map(([id, label]) => <li key={id}><a href={`#${id}`}>{label}</a></li>)}
          </ul>
          <div className="rq-nav-actions">
            <Link href="/login" className="rq-nav-signin">Sign in</Link>
            <Link href="/login" className="rq-btn rq-btn--small">Get started</Link>
          </div>
        </nav>

        <motion.div
          className="rq-intro-block container"
          initial={reduce ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        >
          <p className="rq-kicker">Agent automation for the work your team repeats</p>
          <h1 className="rq-h1">Show it once. Now it’s a tool.</h1>
          <p className="rq-lead">Record a task in any web app. Approve the plan Repliq writes. From then on, an agent runs it, and your other AI agents can too.</p>
          <div className="rq-actions">
            <Link href="/login" className="rq-btn">Get started</Link>
            <a href={DEMO_URL} className="rq-btn rq-btn--quiet" target="_blank" rel="noreferrer">Watch the 60‑second demo</a>
          </div>
        </motion.div>

        <a href="#what" className="rq-scrollcue">
          <span>See one task go from recording to running</span>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 5v14M6 13l6 6 6-6" /></svg>
        </a>
      </header>
    </>
  );
}
