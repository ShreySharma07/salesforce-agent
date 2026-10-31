// app/(marketing)/AmbientMotion.tsx
// Pauses the landing's ambient loops (hero timeline, footer aurora) when they
// are off-screen, and gives visitors a visible control to stop them entirely.
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

  return (
    <button type="button" className="rq-motion-toggle" aria-pressed={stopped} onClick={() => setStopped((s) => !s)}>
      {stopped ? "Play background motion" : "Pause background motion"}
    </button>
  );
}
