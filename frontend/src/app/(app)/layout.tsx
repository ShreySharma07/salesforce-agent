// app/(app)/layout.tsx — auth guard + light-themed app shell (Repliq style).
"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useUser, useLogout } from "@/lib/auth";
import { T } from "@/lib/theme";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: user, isLoading } = useUser();
  const logout = useLogout();

  useEffect(() => {
    if (!isLoading && user === null) router.replace("/login");
  }, [isLoading, user, router]);

  if (isLoading || user === null) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", gap: 10, background: T.pageBg, color: T.body, fontSize: 14 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", animation: "blink 1.6s ease-in-out infinite" }} />
        authenticating…
      </div>
    );
  }

  const nav = [
    { href: "/dashboard", label: "Automations" },
    { href: "/plans", label: "Plans" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: T.pageBg }}>
      <header style={{ position: "sticky", top: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 32px", background: "rgba(255,255,255,0.72)", backdropFilter: "blur(18px)", WebkitBackdropFilter: "blur(18px)", borderBottom: "1px solid rgba(11,18,51,0.06)" }}>
        <Link href="/dashboard" style={{ display: "flex", alignItems: "center", gap: 11, textDecoration: "none" }}>
          <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 30, height: 30, borderRadius: 9, background: T.grad }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#fff" }} />
          </span>
          <span style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.03em", color: T.ink }}>Repliq</span>
        </Link>

        <nav style={{ display: "flex", gap: 28 }}>
          {nav.map((n) => {
            const active = pathname?.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} style={{ fontSize: 15, fontWeight: 500, color: active ? T.violet : T.ink2, textDecoration: "none" }}>
                {n.label}
              </Link>
            );
          })}
        </nav>

        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 13.5, color: T.body }}>{user.email}</span>
          <button
            onClick={async () => { await logout.mutateAsync(); router.replace("/login"); }}
            style={{ background: "#fff", border: "1px solid rgba(11,18,51,0.12)", color: T.ink2, borderRadius: 999, padding: "7px 14px", fontSize: 13.5, cursor: "pointer", fontWeight: 600 }}
          >
            Sign out
          </button>
        </div>
      </header>

      <main style={{ maxWidth: 1100, margin: "0 auto", padding: "40px 24px 80px" }}>{children}</main>
    </div>
  );
}