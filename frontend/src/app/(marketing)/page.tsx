// app/(marketing)/page.tsx
"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ConnectorBand } from "@/components/ConnectorBand";

export default function Landing() {
  return (
    <main className="wrap">
      <nav className="topnav">
        <div className="brand display">
          <span className="dot dot-live" /> agent
        </div>
        <Link href="/login" className="signin">
          Sign in
        </Link>
      </nav>

      <section className="hero">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="eyebrow mono text-dim">
            WATCH ONCE · RUN FOREVER
          </div>
          <h1 className="display">
            An agent that learns your task
            <br />
            from a single recording.
          </h1>
          <p className="lede text-dim">
            Record a workflow once. The agent watches, understands the
            interface, and runs it autonomously in a secure sandbox — and gets
            better every time it runs.
          </p>
          <div className="ctas">
            <Link href="/login" className="primary">
              Get started
            </Link>
            <a href="#how" className="secondary">
              How it works
            </a>
          </div>
        </motion.div>
      </section>

      <motion.section
        className="band-section glass"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3, duration: 0.6 }}
      >
        <div className="band-label text-faint mono">
          OPERATES THE TOOLS YOU ALREADY USE
        </div>
        <ConnectorBand />
      </motion.section>

      <style jsx>{`
        .wrap {
          max-width: 1100px;
          margin: 0 auto;
          padding: 24px;
        }
        .topnav {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 8px 0 64px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 15px;
          letter-spacing: 0.04em;
        }
        .signin {
          color: var(--text-dim);
          text-decoration: none;
          font-size: 14px;
        }
        .signin:hover {
          color: var(--text);
        }
        .hero {
          padding: 40px 0 56px;
        }
        .eyebrow {
          font-size: 11px;
          letter-spacing: 0.18em;
          margin-bottom: 20px;
        }
        h1 {
          font-size: clamp(34px, 6vw, 58px);
          font-weight: 700;
          line-height: 1.04;
          margin: 0 0 22px;
        }
        .lede {
          font-size: 18px;
          line-height: 1.6;
          max-width: 600px;
          margin: 0 0 32px;
        }
        .ctas {
          display: flex;
          gap: 14px;
          align-items: center;
        }
        .primary {
          background: var(--accent);
          color: #04181a;
          text-decoration: none;
          border-radius: 10px;
          padding: 13px 24px;
          font-weight: 600;
          font-size: 15px;
        }
        .secondary {
          color: var(--text);
          text-decoration: none;
          font-size: 15px;
          padding: 13px 8px;
        }
        .secondary:hover {
          color: var(--accent);
        }
        .band-section {
          padding: 22px 0 10px;
        }
        .band-label {
          font-size: 10px;
          letter-spacing: 0.16em;
          text-align: center;
          margin-bottom: 6px;
        }
      `}</style>
    </main>
  );
}