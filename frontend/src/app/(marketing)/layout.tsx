// app/(marketing)/layout.tsx
// Loads the landing's faces and opens the .rq token scope.
import { Rethink_Sans, Onest, JetBrains_Mono } from "next/font/google";

const display = Rethink_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--rq-display", display: "swap" });
const body = Onest({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--rq-body", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--rq-mono", display: "swap" });

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return <div className={`rq ${display.variable} ${body.variable} ${mono.variable}`}>{children}</div>;
}
