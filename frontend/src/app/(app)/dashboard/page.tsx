// app/(app)/dashboard/page.tsx — automations list, Repliq light theme.
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Automation } from "@/lib/types";
import { T, glass, primaryBtn, eyebrow } from "@/lib/theme";

function statusPill(s: Automation["status"]) {
  const map = {
    active: { c: T.ok, bg: T.okBg },
    paused: { c: T.warn, bg: T.warnBg },
    archived: { c: T.faint, bg: "rgba(154,161,189,0.14)" },
  } as const;
  const v = map[s] ?? map.archived;
  return { color: v.c, background: v.bg };
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery<Automation[]>({
    queryKey: ["automations"],
    queryFn: () => api.get<Automation[]>("/automations"),
  });

  return (
    <div>
      <div style={{ marginBottom: 32 }}>
        <span style={{ ...eyebrow, marginBottom: 16 }}>Your workspace</span>
        <h1 style={{ fontSize: 38, fontWeight: 700, letterSpacing: "-0.035em", color: T.ink, margin: "16px 0 8px" }}>Automations</h1>
        <p style={{ fontSize: 18, color: T.body, margin: 0 }}>Tasks the agent has learned and can run on demand.</p>
      </div>

      {isLoading && <div style={{ color: T.dim, padding: "40px 0" }}>Loading your automations…</div>}

      {error && (
        <div style={{ ...glass, padding: 28 }}>
          <div style={{ color: T.bad, fontWeight: 600, marginBottom: 6 }}>Couldn&apos;t load automations.</div>
          <div style={{ color: T.body }}>Check that the backend is running and you&apos;re signed in.</div>
        </div>
      )}

      {data && data.length === 0 && (
        <div style={{ ...glass, padding: 40, textAlign: "center" }}>
          <div style={{ width: 56, height: 56, margin: "0 auto 20px", borderRadius: 16, background: T.grad, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 10px 26px rgba(99,102,241,0.35)" }}>
            <span style={{ width: 20, height: 20, borderRadius: 6, border: "2px solid #fff" }} />
          </div>
          <h2 style={{ fontSize: 22, fontWeight: 700, color: T.ink, margin: "0 0 8px" }}>No automations yet</h2>
          <p style={{ fontSize: 16, color: T.body, margin: "0 0 22px", maxWidth: 420, marginInline: "auto", lineHeight: 1.5 }}>
            Record a task once and approve the plan — it&apos;ll show up here, ready to run.
          </p>
          <Link href="/plans" style={primaryBtn}>Go to plans</Link>
        </div>
      )}

      {data && data.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 20 }}>
          {data.map((a) => (
            <Link key={a.id} href={`/automations/${a.id}`} style={{ ...glass, padding: 24, textDecoration: "none", display: "block", transition: "transform .15s, box-shadow .15s" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span style={{ fontWeight: 700, fontSize: 17, color: T.ink }}>{a.name}</span>
                <span style={{ ...statusPill(a.status), fontSize: 11.5, fontWeight: 600, padding: "3px 10px", borderRadius: 999, textTransform: "uppercase", letterSpacing: "0.04em" }}>{a.status}</span>
              </div>
              {a.description && <p style={{ margin: "0 0 16px", fontSize: 14.5, lineHeight: 1.55, color: T.dim }}>{a.description}</p>}
              <div style={{ display: "flex", gap: 8, fontSize: 13, color: T.faint, fontWeight: 500 }}>
                <span>{a.total_runs} runs</span><span>·</span>
                <span>{a.successful_runs} ok</span>
                {a.last_run_at && (<><span>·</span><span>last {new Date(a.last_run_at).toLocaleDateString()}</span></>)}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}