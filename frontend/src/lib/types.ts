// lib/types.ts
// TypeScript mirrors of the backend Pydantic schemas. Keep in sync with
// app/schemas/*.py on the backend.

export interface UserPublic {
  id: string;
  email: string;
  display_name: string;
  organization_id: string | null;
}

export type AutomationStatus = "active" | "paused" | "archived";

export interface Automation {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  plan_id: string;
  plan_version: number;
  status: AutomationStatus;
  last_run_at: string | null;
  total_runs: number;
  successful_runs: number;
  tags: string[];
}

export type RunStatus =
  | "queued"
  | "provisioning"
  | "running"
  | "paused_for_input"
  | "completed"
  | "completed_with_failures"
  | "failed"
  | "canceled"
  | "budget_exceeded";

export interface Run {
  id: string;
  automation_id: string;
  status: RunStatus;
  live_view_url: string | null;
  started_at: string | null;
  finished_at: string | null;
  summary: string | null;
  error: string | null;
  cost: { llm_calls: number; cost_usd: number };
}

export type PlanStatus =
  | "draft"
  | "pending_approval"
  | "approved"
  | "rejected";

export interface Plan {
  id: string;
  goal: string;
  summary: string | null;
  status: PlanStatus;
  version: number;
  steps: { id: string; kind: string; description: string }[];
}