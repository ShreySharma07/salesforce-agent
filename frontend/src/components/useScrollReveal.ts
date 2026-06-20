// components/useScrollReveal.ts
// Replaces the dc-runtime's [data-reveal] / [data-anim] behavior with a React
// hook. Attach the returned ref to a container; any descendant with the
// `data-reveal` attribute fades + rises in on scroll, and `data-anim`
// elements stagger in once on mount (the hero entrance).

"use client";

import { useEffect, useRef } from "react";

export function useScrollReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;

    const reduce = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    // Hero entrance: stagger [data-anim] in once.
    const animItems = Array.from(
      root.querySelectorAll<HTMLElement>("[data-anim]"),
    );
    animItems.forEach((el, i) => {
      if (reduce) return;
      el.style.opacity = "0";
      el.style.transform = "translateY(40px)";
      el.style.transition =
        "opacity .9s cubic-bezier(.16,1,.3,1), transform .9s cubic-bezier(.16,1,.3,1)";
      el.style.transitionDelay = `${0.1 + i * 0.1}s`;
    });
    requestAnimationFrame(() =>
      animItems.forEach((el) => {
        el.style.opacity = "1";
        el.style.transform = "none";
      }),
    );

    // Scroll reveals.
    const revealItems = Array.from(
      root.querySelectorAll<HTMLElement>("[data-reveal]"),
    );
    if (reduce) {
      revealItems.forEach((el) => (el.style.opacity = "1"));
      return;
    }
    revealItems.forEach((el) => {
      el.style.opacity = "0";
      el.style.transform = "translateY(30px)";
      el.style.transition =
        "opacity .8s cubic-bezier(.16,1,.3,1), transform .8s cubic-bezier(.16,1,.3,1)";
    });
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            (e.target as HTMLElement).style.opacity = "1";
            (e.target as HTMLElement).style.transform = "none";
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12 },
    );
    revealItems.forEach((el) => io.observe(el));

    return () => io.disconnect();
  }, []);

  return ref;
}