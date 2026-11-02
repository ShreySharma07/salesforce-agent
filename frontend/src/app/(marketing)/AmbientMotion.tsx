// app/(marketing)/AmbientMotion.tsx
// Pauses the landing's ambient loops when they are off-screen, and gives
// visitors a small, visible control to stop all background motion.
"use client";

import { useEffect, useState } from "react";

export function AmbientMotion() {
  const [stopped, setStopped] = useState(false);

  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>("[data-ambient]");
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) e.target.toggleAttribute("data-offscreen", !e.isIntersecting);
    });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    document.querySelector(".rq")?.toggleAttribute("data-motion-stopped", stopped);
  }, [stopped]);

  const label = stopped ? "Play background motion" : "Pause background motion";
  return (
    <button type="button" className="rq-motion-toggle" aria-pressed={stopped} aria-label={label} title={label} onClick={() => setStopped((s) => !s)}>
      {stopped ? (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor" /></svg>
      ) : (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M8 5v14M16 5v14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
      )}
    </button>
  );
}
