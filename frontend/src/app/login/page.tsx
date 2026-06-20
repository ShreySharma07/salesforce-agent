// app/login/page.tsx — Repliq light theme, glass card on the gradient hero bg.
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useLogin, useRegister } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { T, glass, primaryBtn } from "@/lib/theme";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const login = useLogin();
  const register = useRegister();
  const busy = login.isPending || register.isPending;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      if (mode === "login") await login.mutateAsync({ email, password });
      else await register.mutateAsync({ email, password, display_name: name });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  }

  const field: React.CSSProperties = {
    background: "#fff", border: "1px solid rgba(11,18,51,0.12)", borderRadius: 11,
    padding: "12px 14px", color: T.ink, fontSize: 15, width: "100%", outline: "none",
  };

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: T.pageBg, position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", top: "8%", left: "10%", width: 460, height: 460, borderRadius: "50%", background: "radial-gradient(circle,rgba(139,92,246,0.32),rgba(139,92,246,0) 70%)", filter: "blur(20px)", pointerEvents: "none" }} />
      <div style={{ position: "absolute", bottom: "4%", right: "8%", width: 500, height: 500, borderRadius: "50%", background: "radial-gradient(circle,rgba(34,211,238,0.26),rgba(34,211,238,0) 70%)", filter: "blur(20px)", pointerEvents: "none" }} />

      <div style={{ ...glass, width: "100%", maxWidth: 440, padding: "40px 40px 30px", position: "relative", zIndex: 2 }}>
        <Link href="/" style={{ display: "flex", alignItems: "center", gap: 11, textDecoration: "none", marginBottom: 28 }}>
          <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 32, height: 32, borderRadius: 9, background: T.grad, boxShadow: "0 6px 16px rgba(99,102,241,0.4)" }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#fff" }} />
          </span>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.03em", color: T.ink }}>Repliq</span>
        </Link>

        <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.03em", color: T.ink, margin: "0 0 8px" }}>
          {mode === "login" ? "Welcome back" : "Create your account"}
        </h1>
        <p style={{ margin: "0 0 28px", fontSize: 15, lineHeight: 1.5, color: T.body }}>
          {mode === "login" ? "Sign in to watch your automations run." : "Watch a task once. Let the agent do it from then on."}
        </p>

        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {mode === "register" && (
            <label style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              <span style={{ fontSize: 13, color: T.ink2, fontWeight: 500 }}>Name</span>
              <input style={field} value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" autoComplete="name" />
            </label>
          )}
          <label style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            <span style={{ fontSize: 13, color: T.ink2, fontWeight: 500 }}>Email</span>
            <input style={field} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" autoComplete="email" required />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            <span style={{ fontSize: 13, color: T.ink2, fontWeight: 500 }}>Password</span>
            <input style={field} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8} required />
          </label>

          {error && (
            <div style={{ fontSize: 13, color: T.bad, background: T.badBg, border: "1px solid rgba(248,113,113,0.3)", borderRadius: 9, padding: "10px 12px" }}>{error}</div>
          )}

          <button type="submit" style={{ ...primaryBtn, width: "100%", padding: 13, marginTop: 4, opacity: busy ? 0.6 : 1 }} disabled={busy}>
            {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div style={{ marginTop: 20, fontSize: 14, textAlign: "center", color: T.body }}>
          {mode === "login" ? (
            <>New here?{" "}<button onClick={() => setMode("register")} style={linkBtn}>Create an account</button></>
          ) : (
            <>Already have an account?{" "}<button onClick={() => setMode("login")} style={linkBtn}>Sign in</button></>
          )}
        </div>
      </div>
    </main>
  );
}

const linkBtn: React.CSSProperties = {
  background: "none", border: "none", color: "#6366f1", cursor: "pointer", fontSize: 14, padding: 0, fontWeight: 600,
};