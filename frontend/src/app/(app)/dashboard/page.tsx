// app/(app)/dashboard/page.tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Automation } from "@/lib/types";

function statusColor(s: Automation["status"]) {
  if (s === "active") return "var(--ok)";
  if (s === "paused") return "var(--warn)";
  return "var(--text-faint)";
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery<Automation[]>({
    queryKey: ["automations"],
    queryFn: () => api.get<Automation[]>("/automations"),
  });

  return (
    <div>
      <div className="head">
        <h1 className="display">Automations</h1>
        <p className="text-dim">
          Tasks the agent has learned and can run on demand.
        </p>
      </div>

      {isLoading && (
        <div className="muted mono text-dim">Loading your automations…</div>
      )}

      {error && (
        <div className="glass errbox">
          <div className="mono" style={{ color: "var(--bad)" }}>
            Couldn&apos;t load automations.
          </div>
          <div className="text-dim">
            Check that the backend is running and you&apos;re signed in.
          </div>
        </div>
      )}

      {data && data.length === 0 && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass empty"
        >
          <h2 className="display">No automations yet</h2>
          <p className="text-dim">
            Record a task once and approve the plan — it&apos;ll show up here,
            ready to run.
          </p>
          <Link href="/plans" className="ghost">
            Go to plans
          </Link>
        </motion.div>
      )}

      {data && data.length > 0 && (
        <div className="grid">
          {data.map((a, i) => (
            <motion.div
              key={a.id}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05, duration: 0.4 }}
            >
              <Link href={`/automations/${a.id}`} className="card glass">
                <div className="card-top">
                  <span className="name">{a.name}</span>
                  <span
                    className="status mono"
                    style={{ color: statusColor(a.status) }}
                  >
                    {a.status}
                  </span>
                </div>
                {a.description && (
                  <p className="desc text-dim">{a.description}</p>
                )}
                <div className="meta mono text-faint">
                  <span>{a.total_runs} runs</span>
                  <span>·</span>
                  <span>{a.successful_runs} ok</span>
                  {a.last_run_at && (
                    <>
                      <span>·</span>
                      <span>
                        last {new Date(a.last_run_at).toLocaleDateString()}
                      </span>
                    </>
                  )}
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}

      <style jsx>{`
        .head {
          margin-bottom: 24px;
        }
        h1 {
          font-size: 26px;
          font-weight: 600;
          margin: 0 0 6px;
        }
        .head p {
          margin: 0;
          font-size: 15px;
        }
        .muted {
          padding: 40px 0;
          font-size: 14px;
        }
        .errbox,
        .empty {
          padding: 28px;
        }
        .empty h2 {
          font-size: 19px;
          margin: 0 0 8px;
        }
        .empty p {
          margin: 0 0 18px;
          font-size: 15px;
        }
        .ghost {
          display: inline-block;
          border: 1px solid var(--accent);
          color: var(--accent);
          text-decoration: none;
          border-radius: 9px;
          padding: 9px 16px;
          font-size: 14px;
        }
        .grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 16px;
        }
        .card {
          display: block;
          padding: 20px;
          text-decoration: none;
          color: var(--text);
          transition:
            transform 0.15s,
            border-color 0.15s;
        }
        .card:hover {
          transform: translateY(-2px);
          border-color: var(--accent);
        }
        .card-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 10px;
        }
        .name {
          font-weight: 600;
          font-size: 16px;
        }
        .status {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .desc {
          margin: 0 0 16px;
          font-size: 14px;
          line-height: 1.5;
        }
        .meta {
          display: flex;
          gap: 8px;
          font-size: 12px;
        }
      `}</style>
    </div>
  );
}