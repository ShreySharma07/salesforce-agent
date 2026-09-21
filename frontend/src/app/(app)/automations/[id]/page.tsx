// app/(app)/automations/[id]/page.tsx — run an automation and watch it work.
//
// The dashboard has always linked here; the page simply did not exist. It is
// the third step of the journey: upload → approve → run.
//
// Run counters are shown split (clean / partial / failed) rather than as a
// single "successful" number, because a run that finished with failed steps
// is not a success and hiding that makes a flaky automation look perfect.
"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Automation, Plan, Run } from "@/lib/types";
import { T, glass, primaryBtn, eyebrow } from "@/lib/theme";

const ACTIVE: string[] = ["queued", "provisioning", "running"];

function runPill(s: Run["status"]) {
  const map: Record<string, { c: string; bg: string }> = {
    completed: { c: T.ok, bg: T.okBg },
    completed_with_failures: { c: T.warn, bg: T.warnBg },
    paused_for_input: { c: T.warn, bg: T.warnBg },
    running: { c: T.violet, bg: "rgba(99,102,241,0.14)" },
    provisioning: { c: T.violet, bg: "rgba(99,102,241,0.14)" },
    queued: { c: T.faint, bg: "rgba(154,161,189,0.14)" },
    failed: { c: T.bad, bg: T.badBg },
    canceled: { c: T.faint, bg: "rgba(154,161,189,0.14)" },
    budget_exceeded: { c: T.bad, bg: T.badBg },
  };
  return map[s] ?? map.queued;
}

function duration(run: Run): string {
  if (!run.started_at) return "—";
  const end = run.finished_at ? new Date(run.finished_at) : new Date();
  const secs = Math.max(0, (end.getTime() - new Date(run.started_at).getTime()) / 1000);
  return secs < 60 ? `${secs.toFixed(0)}s` : `${Math.floor(secs / 60)}m ${(secs % 60).toFixed(0)}s`;
}

export default function AutomationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [resumeFor, setResumeFor] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");

  const { data: auto, isLoading, error } = useQuery<Automation>({
    queryKey: ["automation", id],
    queryFn: () => api.get<Automation>(`/automations/${id}`),
  });

  const { data: plan } = useQuery<Plan>({
    queryKey: ["plan", auto?.plan_id],
    queryFn: () => api.get<Plan>(`/plans/${auto!.plan_id}`),
    enabled: !!auto?.plan_id,
  });

  // Poll while any run is still in flight so the live view link and status
  // update without the user refreshing.
  const { data: runs } = useQuery<Run[]>({
    queryKey: ["runs", id],
    queryFn: () => api.get<Run[]>(`/runs?automation_id=${id}`),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => ACTIVE.includes(r.status)) ? 2000 : false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["runs", id] });
    qc.invalidateQueries({ queryKey: ["automation", id] });
  };

  const start = useMutation({
    mutationFn: () => api.post<Run>(`/automations/${id}/run`),
    onSuccess: invalidate,
  });
  const cancel = useMutation({
    mutationFn: (runId: string) => api.post<Run>(`/runs/${runId}/cancel`),
    onSuccess: invalidate,
  });
  const resume = useMutation({
    mutationFn: (v: { runId: string; response: string }) =>
      api.post<Run>(`/runs/${v.runId}/resume`, { response: v.response }),
    onSuccess: () => { setResumeFor(null); setAnswer(""); invalidate(); },
  });

  if (isLoading) return <div style={{ color: T.dim, padding: "40px 0" }}>Loading automation…</div>;
  if (error || !auto) {
    return (
      <div style={{ ...glass, padding: 28 }}>
        <div style={{ color: T.bad, fontWeight: 600, marginBottom: 6 }}>Couldn&apos;t load this automation.</div>
        <Link href="/dashboard" style={{ color: T.violet }}>Back to automations</Link>
      </div>
    );
  }

  const planReady = plan?.status === "approved";

  return (
    <div>
      <Link href="/dashboard" style={{ fontSize: 14, color: T.violet, textDecoration: "none" }}>
        ← Automations
      </Link>

      <div style={{ margin: "18px 0 32px" }}>
        <span style={{ ...eyebrow, marginBottom: 16 }}>Step 3 of 3</span>
        <h1 style={{ fontSize: 34, fontWeight: 700, letterSpacing: "-0.03em", color: T.ink, margin: "16px 0 8px" }}>
          {auto.name}
        </h1>
        {auto.description && <p style={{ fontSize: 17, color: T.body, margin: 0 }}>{auto.description}</p>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 14, marginBottom: 24 }}>
        {[
          { label: "Total runs", value: auto.total_runs, color: T.ink },
          { label: "Clean", value: auto.successful_runs, color: T.ok },
          { label: "Partial", value: auto.partial_runs ?? 0, color: T.warn },
          { label: "Failed", value: auto.failed_runs ?? 0, color: T.bad },
        ].map((s) => (
          <div key={s.label} style={{ ...glass, padding: 18 }}>
            <div style={{ fontSize: 26, fontWeight: 700, color: s.color }}>{s.value}</div>
            <div style={{ fontSize: 13, color: T.faint, fontWeight: 500 }}>{s.label}</div>
          </div>
        ))}
      </div>

      <div style={{ ...glass, padding: 24, marginBottom: 24 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 16, color: T.ink, marginBottom: 4 }}>
              {plan ? plan.goal : "Linked plan"}
            </div>
            <div style={{ fontSize: 13.5, color: T.faint }}>
              {plan ? `${plan.steps.length} steps · v${plan.version} · ${plan.status.replace(/_/g, " ")}` : auto.plan_id}
            </div>
          </div>
          <button
            onClick={() => start.mutate()}
            disabled={start.isPending || !planReady}
            title={planReady ? "" : "The linked plan must be approved before it can run"}
            style={{ ...primaryBtn, padding: "12px 24px", opacity: start.isPending || !planReady ? 0.55 : 1, cursor: planReady ? "pointer" : "not-allowed" }}
          >
            {start.isPending ? "Starting…" : "Run now"}
          </button>
        </div>
        {!planReady && plan && (
          <div style={{ marginTop: 14, fontSize: 13.5, color: T.warn }}>
            This plan is {plan.status.replace(/_/g, " ")}. Approve it on the{" "}
            <Link href="/plans" style={{ color: T.violet }}>Plans</Link> page before running.
          </div>
        )}
        {start.isError && (
          <div style={{ marginTop: 14, fontSize: 13.5, color: T.bad }}>
            {start.error instanceof ApiError ? start.error.message : "Could not start the run."}
          </div>
        )}
      </div>

      <h2 style={{ fontSize: 20, fontWeight: 700, color: T.ink, margin: "0 0 14px" }}>Runs</h2>

      {runs && runs.length === 0 && (
        <div style={{ ...glass, padding: 32, textAlign: "center", color: T.body }}>
          No runs yet. Press <strong>Run now</strong> to watch the agent work.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {(runs ?? []).map((run) => {
          const pill = runPill(run.status);
          const live = ACTIVE.includes(run.status);
          const steps = run.step_executions ?? [];
          return (
            <div key={run.id} style={{ ...glass, padding: 22 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ color: pill.c, background: pill.bg, fontSize: 11.5, fontWeight: 700, padding: "4px 11px", borderRadius: 999, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                    {run.status.replace(/_/g, " ")}
                  </span>
                  <span style={{ fontSize: 13, color: T.faint, fontFamily: "ui-monospace,monospace" }}>{run.id}</span>
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  {live && run.live_view_url && (
                    <a href={run.live_view_url} target="_blank" rel="noreferrer"
                       style={{ ...primaryBtn, padding: "8px 16px", fontSize: 13.5 }}>
                      Watch live
                    </a>
                  )}
                  {live && (
                    <button onClick={() => cancel.mutate(run.id)} disabled={cancel.isPending}
                      style={{ background: "#fff", border: `1px solid ${T.badBg}`, color: T.bad, borderRadius: 999, padding: "8px 16px", fontSize: 13.5, cursor: "pointer", fontWeight: 600 }}>
                      Cancel
                    </button>
                  )}
                  {run.status === "paused_for_input" && (
                    <button onClick={() => setResumeFor(resumeFor === run.id ? null : run.id)}
                      style={{ ...primaryBtn, padding: "8px 16px", fontSize: 13.5 }}>
                      Answer &amp; resume
                    </button>
                  )}
                </div>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 13, color: T.dim, marginTop: 12 }}>
                <span>{duration(run)}</span>
                {steps.length > 0 && (<><span>·</span><span>{steps.filter((s) => s.status === "succeeded").length}/{steps.length} steps ok</span></>)}
                {run.cost && (<><span>·</span><span>{run.cost.llm_calls} model calls</span>
                  <span>·</span><span>${run.cost.cost_usd.toFixed(4)}</span></>)}
              </div>

              {run.summary && <div style={{ fontSize: 14, color: T.body, marginTop: 10 }}>{run.summary}</div>}

              {run.error && (
                <div style={{ marginTop: 12, padding: 12, borderRadius: 12, background: T.badBg, color: T.ink2, fontSize: 13, fontFamily: "ui-monospace,monospace", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 160, overflow: "auto" }}>
                  {run.error}
                </div>
              )}

              {resumeFor === run.id && (
                <div style={{ marginTop: 14, padding: 16, borderRadius: 14, background: "rgba(99,102,241,0.06)" }}>
                  <div style={{ fontSize: 13.5, color: T.ink2, marginBottom: 10 }}>
                    The agent paused and needs an answer. It will start a fresh browser,
                    sign back in, skip the steps it already completed, and carry on.
                  </div>
                  <textarea
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    placeholder="e.g. use case 00001378"
                    rows={2}
                    style={{ width: "100%", padding: 12, borderRadius: 12, border: "1px solid rgba(11,18,51,0.12)", fontSize: 14, fontFamily: "inherit", resize: "vertical" }}
                  />
                  <button
                    onClick={() => resume.mutate({ runId: run.id, response: answer })}
                    disabled={resume.isPending || !answer.trim()}
                    style={{ ...primaryBtn, marginTop: 10, padding: "9px 18px", fontSize: 14, opacity: resume.isPending || !answer.trim() ? 0.55 : 1 }}
                  >
                    {resume.isPending ? "Resuming…" : "Resume run"}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
