// app/(app)/plans/page.tsx — review, approve, and turn a plan into an automation.
//
// Approving a plan used to be a dead end: the journey stopped there and the
// user had to call the API by hand to get something runnable. Approval now
// leads directly into naming the automation, which is the step that makes the
// plan executable.
"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { Automation, Plan } from "@/lib/types";
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
  const router = useRouter();
  const [naming, setNaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<Plan[]>({
    queryKey: ["plans"],
    queryFn: () => api.get<Plan[]>("/plans"),
  });

  // Used to show which plans are already runnable.
  const { data: automations } = useQuery<Automation[]>({
    queryKey: ["automations"],
    queryFn: () => api.get<Automation[]>("/automations"),
  });
  const automationFor = (planId: string) =>
    (automations ?? []).find((a) => a.plan_id === planId);

  const approve = useMutation({
    mutationFn: (id: string) => api.post<Plan>(`/plans/${id}/approve`),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ["plans"] });
      // Approval alone produces nothing runnable — go straight to naming it.
      setNaming(plan.id);
      setName(plan.goal.slice(0, 60));
    },
  });

  const createAutomation = useMutation({
    mutationFn: (v: { planId: string; name: string }) =>
      api.post<Automation>("/automations", { name: v.name, plan_id: v.planId }),
    onSuccess: (auto) => {
      qc.invalidateQueries({ queryKey: ["automations"] });
      setNaming(null);
      router.push(`/automations/${auto.id}`);
    },
  });

  const correct = useMutation({
    mutationFn: (v: { planId: string; feedback: string }) =>
      api.post<Plan>(`/plans/${v.planId}/correct`, { feedback: v.feedback }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plans"] });
      setCorrecting(null);
      setFeedback("");
    },
  });

  return (
    <div>
      <div style={{ marginBottom: 32 }}>
        <span style={{ ...eyebrow, marginBottom: 16 }}>Step 2 of 3</span>
        <h1 style={{ fontSize: 38, fontWeight: 700, letterSpacing: "-0.035em", color: T.ink, margin: "16px 0 8px" }}>Plans</h1>
        <p style={{ fontSize: 18, color: T.body, margin: 0 }}>
          Generated from your recordings. Read one, correct it in plain language, then approve it.
        </p>
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
          <p style={{ fontSize: 16, color: T.body, margin: "0 0 22px", maxWidth: 440, marginInline: "auto", lineHeight: 1.5 }}>
            Record a task and upload it — the agent turns the recording into a plan you can review here.
          </p>
          <Link href="/upload" style={primaryBtn}>Upload a recording</Link>
        </div>
      )}

      {data && data.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {data.map((p) => {
            const pill = statusPill(p.status);
            const auto = automationFor(p.id);
            const isOpen = expanded === p.id;
            return (
              <div key={p.id} style={{ ...glass, padding: 24 }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
                      <span style={{ fontWeight: 700, fontSize: 17, color: T.ink }}>{p.goal || "Untitled plan"}</span>
                      <span style={{ color: pill.c, background: pill.bg, fontSize: 11.5, fontWeight: 600, padding: "3px 10px", borderRadius: 999, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {p.status.replace(/_/g, " ")}
                      </span>
                    </div>
                    {p.summary && <p style={{ margin: "0 0 12px", fontSize: 14.5, color: T.dim, lineHeight: 1.55 }}>{p.summary}</p>}
                    <div style={{ fontSize: 13, color: T.faint, display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button onClick={() => setExpanded(isOpen ? null : p.id)}
                        style={{ background: "none", border: "none", padding: 0, color: T.violet, cursor: "pointer", fontSize: 13, fontWeight: 600 }}>
                        {isOpen ? "Hide steps" : `${p.steps?.length ?? 0} steps`}
                      </button>
                      <span>·</span><span>v{p.version}</span>
                      {auto && (<><span>·</span>
                        <Link href={`/automations/${auto.id}`} style={{ color: T.violet }}>automation ready</Link></>)}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    {p.status !== "approved" && (
                      <button onClick={() => setCorrecting(correcting === p.id ? null : p.id)}
                        style={{ background: "#fff", border: "1px solid rgba(11,18,51,0.12)", color: T.ink2, borderRadius: 999, padding: "10px 18px", fontSize: 14, cursor: "pointer", fontWeight: 600 }}>
                        Correct
                      </button>
                    )}
                    {p.status !== "approved" && (
                      <button onClick={() => approve.mutate(p.id)} disabled={approve.isPending}
                        style={{ ...primaryBtn, padding: "10px 20px", fontSize: 14, opacity: approve.isPending ? 0.6 : 1 }}>
                        {approve.isPending ? "Approving…" : "Approve"}
                      </button>
                    )}
                    {p.status === "approved" && !auto && naming !== p.id && (
                      <button onClick={() => { setNaming(p.id); setName(p.goal.slice(0, 60)); }}
                        style={{ ...primaryBtn, padding: "10px 20px", fontSize: 14 }}>
                        Create automation
                      </button>
                    )}
                    {auto && (
                      <Link href={`/automations/${auto.id}`} style={{ ...primaryBtn, padding: "10px 20px", fontSize: 14 }}>
                        Open automation
                      </Link>
                    )}
                  </div>
                </div>

                {isOpen && (
                  <ol style={{ margin: "16px 0 0", paddingLeft: 20, fontSize: 14, color: T.body, lineHeight: 1.7 }}>
                    {(p.steps ?? []).map((s) => (
                      <li key={s.id}>
                        <span style={{ color: T.faint, fontFamily: "ui-monospace,monospace", fontSize: 12 }}>{s.kind}</span>{" "}
                        {s.description}
                      </li>
                    ))}
                  </ol>
                )}

                {naming === p.id && (
                  <div style={{ marginTop: 16, padding: 16, borderRadius: 14, background: "rgba(99,102,241,0.06)" }}>
                    <div style={{ fontSize: 13.5, color: T.ink2, marginBottom: 10 }}>
                      Name this automation so you can find it on the dashboard.
                    </div>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <input value={name} onChange={(e) => setName(e.target.value)}
                        placeholder="Daily case triage"
                        style={{ flex: 1, minWidth: 220, padding: "11px 14px", borderRadius: 12, border: "1px solid rgba(11,18,51,0.12)", fontSize: 14, fontFamily: "inherit" }} />
                      <button onClick={() => createAutomation.mutate({ planId: p.id, name })}
                        disabled={createAutomation.isPending || !name.trim()}
                        style={{ ...primaryBtn, padding: "11px 20px", fontSize: 14, opacity: createAutomation.isPending || !name.trim() ? 0.6 : 1 }}>
                        {createAutomation.isPending ? "Creating…" : "Create"}
                      </button>
                      <button onClick={() => setNaming(null)}
                        style={{ background: "#fff", border: "1px solid rgba(11,18,51,0.12)", color: T.ink2, borderRadius: 999, padding: "11px 18px", fontSize: 14, cursor: "pointer", fontWeight: 600 }}>
                        Later
                      </button>
                    </div>
                    {createAutomation.isError && (
                      <div style={{ marginTop: 10, fontSize: 13.5, color: T.bad }}>
                        {createAutomation.error instanceof ApiError ? createAutomation.error.message : "Could not create the automation."}
                      </div>
                    )}
                  </div>
                )}

                {correcting === p.id && (
                  <div style={{ marginTop: 16, padding: 16, borderRadius: 14, background: "rgba(99,102,241,0.06)" }}>
                    <div style={{ fontSize: 13.5, color: T.ink2, marginBottom: 10 }}>
                      Describe what to change in plain language. The agent rewrites the plan;
                      you never edit steps by hand.
                    </div>
                    <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)}
                      placeholder="do this for every new case, not just 00001378"
                      rows={3}
                      style={{ width: "100%", padding: 12, borderRadius: 12, border: "1px solid rgba(11,18,51,0.12)", fontSize: 14, fontFamily: "inherit", resize: "vertical" }} />
                    <button onClick={() => correct.mutate({ planId: p.id, feedback })}
                      disabled={correct.isPending || !feedback.trim()}
                      style={{ ...primaryBtn, marginTop: 10, padding: "10px 18px", fontSize: 14, opacity: correct.isPending || !feedback.trim() ? 0.6 : 1 }}>
                      {correct.isPending ? "Regenerating…" : "Regenerate plan"}
                    </button>
                    {correct.isError && (
                      <div style={{ marginTop: 10, fontSize: 13.5, color: T.bad }}>
                        {correct.error instanceof ApiError ? correct.error.message : "Could not regenerate the plan."}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
