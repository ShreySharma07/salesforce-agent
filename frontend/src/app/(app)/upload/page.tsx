// app/(app)/upload/page.tsx — record → plan. The entry point of the product.
//
// The backend runs the pipeline in the background, so this page uploads once
// and then polls `GET /videos/{id}` until a plan_id appears. It deliberately
// surfaces narration problems in full: a recording whose audio failed to
// transcribe still yields a plan, but that plan is missing every spoken rule,
// and the user needs to know that before approving it.
"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { VideoStatus } from "@/lib/types";
import { T, glass, primaryBtn, eyebrow } from "@/lib/theme";

const TERMINAL = ["completed", "failed"];

export default function UploadPage() {
  const [videoId, setVideoId] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useMutation({
    mutationFn: (f: File) => api.upload<VideoStatus>("/videos", f),
    onSuccess: (v) => setVideoId(v.video_id),
  });

  // Poll while the pipeline runs; stop as soon as it reaches a terminal state.
  const { data: status } = useQuery<VideoStatus>({
    queryKey: ["video", videoId],
    queryFn: () => api.get<VideoStatus>(`/videos/${videoId}`),
    enabled: !!videoId,
    refetchInterval: (q) =>
      q.state.data && TERMINAL.includes(q.state.data.status) ? false : 2000,
  });

  useEffect(() => {
    if (file && !upload.isPending && !videoId) upload.mutate(file);
  }, [file, upload, videoId]);

  const reset = () => {
    setVideoId(null);
    setFile(null);
    upload.reset();
    if (inputRef.current) inputRef.current.value = "";
  };

  const busy = upload.isPending || (!!status && !TERMINAL.includes(status.status));

  return (
    <div>
      <div style={{ marginBottom: 32 }}>
        <span style={{ ...eyebrow, marginBottom: 16 }}>Step 1 of 3</span>
        <h1 style={{ fontSize: 38, fontWeight: 700, letterSpacing: "-0.035em", color: T.ink, margin: "16px 0 8px" }}>
          Upload a recording
        </h1>
        <p style={{ fontSize: 18, color: T.body, margin: 0, maxWidth: 640, lineHeight: 1.55 }}>
          Record yourself doing the task once, narrating the rules out loud. The agent
          watches the recording and writes a plan you can review.
        </p>
      </div>

      {!videoId && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const f = e.dataTransfer.files?.[0];
            if (f) setFile(f);
          }}
          onClick={() => inputRef.current?.click()}
          style={{
            ...glass,
            padding: 48,
            textAlign: "center",
            cursor: "pointer",
            border: dragging ? `2px dashed ${T.violet}` : "2px dashed rgba(99,102,241,0.25)",
            background: dragging ? "rgba(99,102,241,0.06)" : glass.background,
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv,.m4v"
            style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); }}
          />
          <div style={{ width: 56, height: 56, margin: "0 auto 20px", borderRadius: 16, background: T.grad, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 10px 26px rgba(99,102,241,0.35)" }}>
            <span style={{ width: 18, height: 18, borderRadius: 4, border: "2px solid #fff", borderBottomColor: "transparent" }} />
          </div>
          <h2 style={{ fontSize: 21, fontWeight: 700, color: T.ink, margin: "0 0 8px" }}>
            {upload.isPending ? "Uploading…" : "Drop a screen recording here"}
          </h2>
          <p style={{ fontSize: 15, color: T.body, margin: 0 }}>
            or click to choose a file · mp4, mov, webm, mkv · up to 512 MB
          </p>
        </div>
      )}

      {upload.isError && (
        <div style={{ ...glass, padding: 24, marginTop: 20, borderLeft: `4px solid ${T.bad}` }}>
          <div style={{ color: T.bad, fontWeight: 600, marginBottom: 6 }}>Upload failed</div>
          <div style={{ color: T.body, fontSize: 14.5 }}>
            {upload.error instanceof ApiError ? upload.error.message : "Unknown error"}
          </div>
          <button onClick={reset} style={{ ...primaryBtn, marginTop: 16, padding: "10px 20px", fontSize: 14 }}>
            Try again
          </button>
        </div>
      )}

      {status && (
        <div style={{ ...glass, padding: 28, marginTop: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            {busy && (
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: T.violet, animation: "blink 1.6s ease-in-out infinite" }} />
            )}
            <span style={{ fontWeight: 700, fontSize: 17, color: T.ink }}>
              {status.status === "completed" ? "Plan ready for review"
                : status.status === "failed" ? "Processing failed"
                : (status.stage || "Processing…")}
            </span>
          </div>

          <div style={{ fontSize: 14, color: T.dim, display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
            <span>{status.filename}</span>
            {status.frame_count > 0 && (<><span>·</span><span>{status.frame_count} keyframes</span></>)}
            {status.narration_segments > 0 && (
              <><span>·</span><span>{status.narration_segments} narration segments</span></>
            )}
          </div>

          {/* Narration problems are shown as prominently as failures: a plan
              built without the spoken rules looks fine but does the wrong thing. */}
          {status.narration_warning && (
            <div style={{ marginTop: 18, padding: 16, borderRadius: 14, background: T.warnBg, borderLeft: `4px solid ${T.warn}` }}>
              <div style={{ color: T.warn, fontWeight: 700, fontSize: 14, marginBottom: 6 }}>
                {status.narration_approximate ? "Narration timing is approximate" : "Narration was not captured"}
              </div>
              <div style={{ color: T.ink2, fontSize: 14, lineHeight: 1.55 }}>{status.narration_warning}</div>
              {status.narration_detail && (
                <div style={{ color: T.faint, fontSize: 12.5, marginTop: 8 }}>{status.narration_detail}</div>
              )}
            </div>
          )}

          {status.status === "failed" && (
            <div style={{ marginTop: 18, padding: 16, borderRadius: 14, background: T.badBg, borderLeft: `4px solid ${T.bad}` }}>
              <div style={{ color: T.bad, fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Pipeline error</div>
              <div style={{ color: T.ink2, fontSize: 14, fontFamily: "ui-monospace,monospace", wordBreak: "break-word" }}>
                {status.error}
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 12, marginTop: 22 }}>
            {status.plan_id && (
              <Link href="/plans" style={{ ...primaryBtn, padding: "11px 22px", fontSize: 14.5 }}>
                Review the plan
              </Link>
            )}
            {TERMINAL.includes(status.status) && (
              <button
                onClick={reset}
                style={{ background: "#fff", border: "1px solid rgba(11,18,51,0.12)", color: T.ink2, borderRadius: 999, padding: "11px 22px", fontSize: 14.5, cursor: "pointer", fontWeight: 600 }}
              >
                Upload another
              </button>
            )}
          </div>
        </div>
      )}

      <div style={{ marginTop: 28, fontSize: 13.5, color: T.faint, lineHeight: 1.6, maxWidth: 640 }}>
        Nothing runs automatically. The recording becomes a plan that stays in review
        until you approve it, and only an approved plan can be turned into an automation.
      </div>
    </div>
  );
}
