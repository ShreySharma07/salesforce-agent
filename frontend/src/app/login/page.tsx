// app/login/page.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { useLogin, useRegister } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { ConnectorBand } from "@/components/ConnectorBand";

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
      if (mode === "login") {
        await login.mutateAsync({ email, password });
      } else {
        await register.mutateAsync({ email, password, display_name: name });
      }
      router.push("/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong.",
      );
    }
  }

  return (
    <main className="auth-wrap">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="auth-card glass"
      >
        <div className="brand display">
          <span className="dot dot-live" /> agent
        </div>
        <h1 className="display">
          {mode === "login" ? "Welcome back" : "Create your account"}
        </h1>
        <p className="text-dim sub">
          {mode === "login"
            ? "Sign in to watch your automations run."
            : "Watch a task once. Let the agent do it from then on."}
        </p>

        <form onSubmit={submit} className="form">
          {mode === "register" && (
            <label className="field">
              <span>Name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ada Lovelace"
                autoComplete="name"
              />
            </label>
          )}
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
              required
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              minLength={8}
              required
            />
          </label>

          {error && <div className="err mono">{error}</div>}

          <button type="submit" className="cta" disabled={busy}>
            {busy
              ? "Working…"
              : mode === "login"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <div className="switch text-dim">
          {mode === "login" ? (
            <>
              New here?{" "}
              <button onClick={() => setMode("register")}>
                Create an account
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button onClick={() => setMode("login")}>Sign in</button>
            </>
          )}
        </div>

        <div className="band-mount">
          <div className="band-label text-faint mono">CONNECTS WITH</div>
          <ConnectorBand />
        </div>
      </motion.div>

      <style jsx>{`
        .auth-wrap {
          min-height: 100vh;
          display: grid;
          place-items: center;
          padding: 24px;
        }
        .auth-card {
          width: 100%;
          max-width: 460px;
          padding: 40px 40px 24px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 9px;
          font-size: 15px;
          letter-spacing: 0.04em;
          color: var(--text);
          margin-bottom: 28px;
        }
        h1 {
          font-size: 28px;
          font-weight: 600;
          margin: 0 0 8px;
        }
        .sub {
          margin: 0 0 28px;
          font-size: 15px;
          line-height: 1.5;
        }
        .form {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 7px;
        }
        .field span {
          font-size: 13px;
          color: var(--text-dim);
        }
        .field input {
          background: rgba(10, 14, 20, 0.6);
          border: 1px solid var(--panel-border);
          border-radius: 10px;
          padding: 12px 14px;
          color: var(--text);
          font-size: 15px;
          font-family: var(--font-body);
          transition: border-color 0.15s;
        }
        .field input:focus {
          outline: none;
          border-color: var(--accent);
        }
        .err {
          font-size: 13px;
          color: var(--bad);
          background: rgba(248, 113, 113, 0.08);
          border: 1px solid rgba(248, 113, 113, 0.2);
          border-radius: 8px;
          padding: 10px 12px;
        }
        .cta {
          margin-top: 4px;
          background: var(--accent);
          color: #04181a;
          border: none;
          border-radius: 10px;
          padding: 13px;
          font-size: 15px;
          font-weight: 600;
          cursor: pointer;
          transition:
            transform 0.1s,
            filter 0.15s;
        }
        .cta:hover:not(:disabled) {
          filter: brightness(1.08);
        }
        .cta:active:not(:disabled) {
          transform: translateY(1px);
        }
        .cta:disabled {
          opacity: 0.6;
          cursor: default;
        }
        .switch {
          margin-top: 20px;
          font-size: 14px;
          text-align: center;
        }
        .switch button {
          background: none;
          border: none;
          color: var(--accent);
          cursor: pointer;
          font-size: 14px;
          padding: 0;
        }
        .band-mount {
          margin: 28px -40px 0;
          border-top: 1px solid var(--panel-border);
          padding-top: 12px;
        }
        .band-label {
          font-size: 10px;
          letter-spacing: 0.16em;
          text-align: center;
          margin-bottom: 4px;
        }
      `}</style>
    </main>
  );
}