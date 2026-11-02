// app/(marketing)/page.tsx
// Repliq landing: a fluted-glass hero, then solid layers telling one story:
// what it does, the impact, what it connects to, how it works, the footer.
import type { Metadata } from "next";
import "./landing.css";
import { StoryProvider } from "./story";
import { Hero } from "./Hero";
import { Below } from "./Sections";

export const metadata: Metadata = {
  title: "Repliq: show it once, now it’s a tool",
  description:
    "Record a task in any web app, approve the plan, and Repliq’s agents run it. Your other AI agents can use it too.",
};

export default function Landing() {
  return (
    <StoryProvider>
      <a href="#main" className="rq-skip">Skip to content</a>
      <Hero />
      <Below />
    </StoryProvider>
  );
}
