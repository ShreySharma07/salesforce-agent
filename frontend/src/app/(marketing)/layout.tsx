// app/(marketing)/layout.tsx
// Loads the landing's two faces and opens the .rq token scope.
import { Rethink_Sans, Onest } from "next/font/google";

const display = Rethink_Sans({ subsets: ["latin"], weight: ["600"], variable: "--rq-display", display: "swap" });
const body = Onest({ subsets: ["latin"], weight: ["400", "600"], variable: "--rq-body", display: "swap" });

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return <div className={`rq ${display.variable} ${body.variable}`}>{children}</div>;
}
