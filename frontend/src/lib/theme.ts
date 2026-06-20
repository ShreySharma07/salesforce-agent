// lib/theme.ts
// Shared Repliq design tokens so login / dashboard / plans all match the
// landing's light, Stripe-style aesthetic. Inline-style objects (the codebase
// already styles inline), so any page can import and spread these.

export const T = {
  ink: "#0b1233",
  ink2: "#42496b",
  body: "#4a5578",
  dim: "#5a6488",
  faint: "#9aa1bd",
  violet: "#6366f1",
  violet2: "#8b5cf6",
  cyan: "#22d3ee",
  ok: "#16a34a",
  okBg: "rgba(34,197,94,0.14)",
  warn: "#b45309",
  warnBg: "rgba(251,191,36,0.16)",
  bad: "#dc2626",
  badBg: "rgba(248,113,113,0.12)",
  pageBg: "linear-gradient(180deg,#ffffff 0%,#f5f4ff 55%,#edefff 100%)",
  grad: "linear-gradient(135deg,#6366f1,#8b5cf6)",
  gradText:
    "linear-gradient(120deg,#6366f1,#8b5cf6 55%,#22d3ee)",
} as const;

// A glass panel base (light).
export const glass: React.CSSProperties = {
  background: "rgba(255,255,255,0.6)",
  backdropFilter: "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
  border: "1px solid rgba(255,255,255,0.8)",
  boxShadow: "0 18px 44px rgba(40,30,90,0.08)",
  borderRadius: 22,
};

// The primary pill button.
export const primaryBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 9,
  fontSize: 15,
  fontWeight: 600,
  color: "#fff",
  textDecoration: "none",
  padding: "13px 24px",
  borderRadius: 999,
  background: T.grad,
  boxShadow: "0 10px 28px rgba(99,102,241,0.38)",
  border: "none",
  cursor: "pointer",
};

export const eyebrow: React.CSSProperties = {
  display: "inline-block",
  padding: "7px 16px",
  borderRadius: 999,
  background: "rgba(99,102,241,0.10)",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  color: "#5b54e6",
};