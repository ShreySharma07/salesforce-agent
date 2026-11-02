// app/(marketing)/story.tsx
// The visitor's choices in the hero (rule answer, approval) carried down the
// page, so later sections can echo them: the correction, the tool, the run.
"use client";

import { createContext, useContext, useMemo, useState } from "react";

export type Answer = "every" | "once" | null;

type Story = {
  answer: Answer;
  setAnswer: (a: Answer) => void;
  approved: boolean;
  setApproved: (v: boolean) => void;
};

const StoryContext = createContext<Story | null>(null);

export function StoryProvider({ children }: { children: React.ReactNode }) {
  const [answer, setAnswer] = useState<Answer>(null);
  const [approved, setApproved] = useState(false);
  const value = useMemo(() => ({ answer, setAnswer, approved, setApproved }), [answer, approved]);
  return <StoryContext.Provider value={value}>{children}</StoryContext.Provider>;
}

export function useStory() {
  const s = useContext(StoryContext);
  if (!s) throw new Error("useStory must be used inside StoryProvider");
  return s;
}

/** The rule in plain words, for echoing later on the page. */
export function ruleText(answer: Answer) {
  if (answer === "every") return "Every urgent case gets High priority";
  if (answer === "once") return "High priority only when you say so";
  return "Urgent cases on open deals get High priority";
}
