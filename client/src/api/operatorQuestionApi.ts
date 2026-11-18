import { request } from "./http";

/** One question an agent asked the operator, as `GET /operator-questions` returns it. */
export type OperatorQuestionRow = {
  id: string;
  stage_key: string | null;
  question: string;
  options: string[];
  default: string;
  confidence: "low" | "medium" | "high";
  reversibility: "easy" | "costly" | "irreversible";
  affects: string;
  context: string;
  status: "pending" | "approved" | "rejected" | "ignored";
  /** "confirmed" or "overridden" once answered; empty while open or when ignored. */
  outcome: "" | "confirmed" | "overridden";
  answer: string;
  holds_gate: boolean;
  created_at: string;
  resolved_at: string | null;
};

export type OperatorQuestionsFeed = {
  ticket: string;
  open: number;
  holding_gate: number;
  total: number;
  questions: OperatorQuestionRow[];
};

export const operatorQuestionApi = {
  ticketOperatorQuestions: (ticketId: string) =>
    request<OperatorQuestionsFeed>(`/api/tickets/${ticketId}/operator-questions`),
};
