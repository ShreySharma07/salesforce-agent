// app/(app)/layout.tsx
// Auth guard for the whole app island. Calls /auth/me; while loading shows a
// quiet state; if unauthenticated, redirects to /login. Every nested route
// (dashboard, runs, settings) is protected by sitting under this layout.

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useUser, useLogout } from "@/lib/auth";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { data: user, isLoading } = useUser();
  const logout = useLogout();

  useEffect(() => {
    if (!isLoading && user === null) router.replace("/login");
  }, [isLoading, user, router]);

  if (isLoading || user === null) {
    return (
      <div className="boot">
        <span className="dot dot-live" />
        <span className="mono text-dim">authenticating…</span>
        <style jsx>{`
          .boot {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            font-size: 14px;
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar glass">
        <div className="brand display">
          <span className="dot dot-live" /> agent
        </div>
        <nav className="nav mono">
          <a href="/dashboard">Automations</a>
          <a href="/plans">Plans</a>
        </nav>
        <div className="user">
          <span className="text-dim mono">{user.email}</span>
          <button
            onClick={async () => {
              await logout.mutateAsync();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="content">{children}</main>

      <style jsx>{`
        .shell {
          min-height: 100vh;
          padding: 20px;
          max-width: 1100px;
          margin: 0 auto;
        }
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 14px 20px;
          margin-bottom: 24px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 15px;
          letter-spacing: 0.04em;
        }
        .nav {
          display: flex;
          gap: 22px;
          font-size: 13px;
        }
        .nav a {
          color: var(--text-dim);
          text-decoration: none;
          transition: color 0.15s;
        }
        .nav a:hover {
          color: var(--text);
        }
        .user {
          display: flex;
          align-items: center;
          gap: 14px;
          font-size: 13px;
        }
        .user button {
          background: none;
          border: 1px solid var(--panel-border);
          color: var(--text-dim);
          border-radius: 8px;
          padding: 6px 12px;
          font-size: 13px;
          cursor: pointer;
          transition:
            color 0.15s,
            border-color 0.15s;
        }
        .user button:hover {
          color: var(--text);
          border-color: var(--accent);
        }
      `}</style>
    </div>
  );
}