// app/(app)/plans/page.tsx — plans list with approve action, Repliq light theme.
"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { CapabilityPanel } from "@/components/CapabilityPanel";
import type { Plan } from "@/lib/types";
import { T, glass, primaryBtn, eyebrow } from "@/lib/theme";

function statusPill(s: Plan["status"]) {
  const map: Record<string, { c: string; bg: string }> = {
    approved: { c: T.ok, bg: T.okBg },
    pending_approval: { c: T.warn, bg: T.warnBg },
    draft: { c: T.faint, bg: "rgba(154,161,189,0.14)" },
    rejected: { c: T.bad, bg: T.badBg },
  };
  return map[s] ?? map.draft;
}

export default function PlansPage() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery<Plan[]>({
    queryKey: ["plans"],
    queryFn: () => api.get<Plan[]>("/plans"),
  });

  // Which plans show their capability check. Plans awaiting approval show it
  // by default, since that is when a missing field or permission matters.
  const [checkOpen, setCheckOpen] = useState<Record<string, boolean>>({});
  const isOpen = (p: Plan) => checkOpen[p.id] ?? p.status === "pending_approval";

  const approve = useMutation({
    mutationFn: (id: string) => api.post<Plan>(`/plans/${id}/approve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plans"] }),
  });

  return (
    <div>
      <div style={{ marginBottom: 32 }}>
        <span style={{ ...eyebrow, marginBottom: 16 }}>Recorded tasks</span>
        <h1 style={{ fontSize: 38, fontWeight: 700, letterSpacing: "-0.035em", color: T.ink, margin: "16px 0 8px" }}>Plans</h1>
        <p style={{ fontSize: 18, color: T.body, margin: 0 }}>Generated from your recordings. Approve one to turn it into an automation.</p>
      </div>

      {isLoading && <div style={{ color: T.dim, padding: "40px 0" }}>Loading plans…</div>}

      {error && (
        <div style={{ ...glass, padding: 28 }}>
          <div style={{ color: T.bad, fontWeight: 600 }}>Couldn&apos;t load plans.</div>
        </div>
      )}

      {data && data.length === 0 && (
        <div style={{ ...glass, padding: 40, textAlign: "center" }}>
          <h2 style={{ fontSize: 22, fontWeight: 700, color: T.ink, margin: "0 0 8px" }}>No plans yet</h2>
          <p style={{ fontSize: 16, color: T.body, margin: 0, maxWidth: 440, marginInline: "auto", lineHeight: 1.5 }}>
            Record a task and upload it — the agent turns the recording into a plan you can review here.
          </p>
        </div>
      )}

      {data && data.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {data.map((p) => {
            const pill = statusPill(p.status);
            return (
              <div key={p.id} style={{ ...glass, padding: 24 }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: 17, color: T.ink }}>{p.goal || "Untitled plan"}</span>
                      <span style={{ color: pill.c, background: pill.bg, fontSize: 11.5, fontWeight: 600, padding: "3px 10px", borderRadius: 999, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {p.status.replace(/_/g, " ")}
                      </span>
                    </div>
                    {p.summary && <p style={{ margin: "0 0 12px", fontSize: 14.5, color: T.dim, lineHeight: 1.55 }}>{p.summary}</p>}
                    <div style={{ fontSize: 13, color: T.faint }}>
                      {p.steps?.length ?? 0} steps · v{p.version}
                      {" · "}
                      <button
                        onClick={() => setCheckOpen((s) => ({ ...s, [p.id]: !isOpen(p) }))}
                        style={{ fontSize: 13, fontWeight: 600, color: T.violet, background: "transparent", border: "none", padding: 0, cursor: "pointer" }}
                      >
                        {isOpen(p) ? "Hide capability check" : "Check against my org"}
                      </button>
                    </div>
                  </div>
                  {p.status !== "approved" && (
                    <button
                      onClick={() => approve.mutate(p.id)}
                      disabled={approve.isPending}
                      style={{ ...primaryBtn, padding: "10px 20px", fontSize: 14, opacity: approve.isPending ? 0.6 : 1 }}
                    >
                      {approve.isPending ? "Approving…" : "Approve"}
                    </button>
                  )}
                </div>
                {isOpen(p) && <CapabilityPanel planId={p.id} version={p.version} />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}