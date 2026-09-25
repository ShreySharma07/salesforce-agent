// components/CapabilityPanel.tsx — "for this org I have these, I lack those".
// Shows GET /plans/{id}/capabilities as a have / don't-have diff so a plan's
// gaps (missing fields, picklist values, permissions, tools) surface before
// approval instead of mid-run.
"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CapabilityCategory, CapabilityCheck, CapabilityReport } from "@/lib/types";
import { T } from "@/lib/theme";

const CATEGORY_LABEL: Record<CapabilityCategory, string> = {
  org: "Org metadata",
  permission: "Your permissions",
  agent: "Agent tools",
};
const CATEGORIES: CapabilityCategory[] = ["org", "permission", "agent"];

function Row({ c }: { c: CapabilityCheck }) {
  return (
    <li style={{ padding: "8px 0", borderTop: "1px solid rgba(11,18,51,0.06)" }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: T.ink }}>{c.name}</div>
      {c.detail && <div style={{ fontSize: 12.5, color: T.dim, lineHeight: 1.45, marginTop: 2 }}>{c.detail}</div>}
      {c.steps.length > 0 && (
        <div style={{ fontSize: 11.5, color: T.faint, marginTop: 2 }}>used by {c.steps.join(", ")}</div>
      )}
    </li>
  );
}

function Column({ title, color, bg, checks }: { title: string; color: string; bg: string; checks: CapabilityCheck[] }) {
  return (
    <div style={{ flex: "1 1 280px", minWidth: 0 }}>
      <div style={{ display: "inline-block", color, background: bg, fontSize: 12, fontWeight: 700, padding: "3px 10px", borderRadius: 999, marginBottom: 6 }}>
        {title} · {checks.length}
      </div>
      {checks.length === 0 ? (
        <div style={{ fontSize: 13, color: T.faint, padding: "8px 0" }}>Nothing here.</div>
      ) : (
        CATEGORIES.map((cat) => {
          const rows = checks.filter((c) => c.category === cat);
          if (rows.length === 0) return null;
          return (
            <div key={cat} style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: T.faint, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {CATEGORY_LABEL[cat]}
              </div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {rows.map((c, i) => <Row key={`${c.category}-${c.name}-${i}`} c={c} />)}
              </ul>
            </div>
          );
        })
      )}
    </div>
  );
}

export function CapabilityPanel({ planId, version }: { planId: string; version: number }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery<CapabilityReport>({
    queryKey: ["capabilities", planId, version],
    queryFn: () => api.get<CapabilityReport>(`/plans/${planId}/capabilities`),
    staleTime: 60_000,
  });

  if (isLoading) return <div style={{ fontSize: 13.5, color: T.dim, padding: "12px 0" }}>Checking this plan against your org…</div>;
  if (error || !data) {
    return <div style={{ fontSize: 13.5, color: T.bad, padding: "12px 0" }}>Couldn&apos;t check capabilities.</div>;
  }

  const have = data.checks.filter((c) => c.status === "available");
  const lack = data.checks.filter((c) => c.status === "missing");
  const unknown = data.checks.filter((c) => c.status === "unknown");

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid rgba(11,18,51,0.08)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
        <div style={{ fontSize: 13.5, color: T.body }}>
          <strong style={{ color: T.ink }}>Capability check</strong>
          {" · "}
          {data.connected && data.org_url ? data.org_url.replace(/^https?:\/\//, "") : `${data.app} not connected`}
          {" · "}
          {lack.length === 0 ? "nothing missing" : `${lack.length} missing`}
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          style={{ fontSize: 12.5, fontWeight: 600, color: T.violet, background: "transparent", border: "none", cursor: "pointer", opacity: isFetching ? 0.5 : 1 }}
        >
          {isFetching ? "Re-checking…" : "Re-check"}
        </button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 24 }}>
        <Column title="Don't have" color={T.bad} bg={T.badBg} checks={lack} />
        <Column title="Have" color={T.ok} bg={T.okBg} checks={have} />
      </div>
      {unknown.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Column title="Couldn't verify" color={T.warn} bg={T.warnBg} checks={unknown} />
        </div>
      )}
    </div>
  );
}
