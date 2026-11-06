import { request } from "./http";

/** Mirrors `LandingPhase` in `models/domain/landing_enums.py`. */
export type LandingPhase = "not_landing" | "merging" | "waiting_for_capacity" | "verifying";

/** The `GateOutcome` values a refused land verification can carry. */
export type RefusalOutcome = "failed" | "unavailable";

/** Mirrors `LeaseAhead` in `services/landing_status.py`. */
export interface LeaseAhead {
  label: string;
  held: boolean;
  since: string | null;
  progress: string;
}

/** Mirrors `RefusedAttempt` in `services/landing_status.py`. */
export interface RefusedAttempt {
  at: string | null;
  outcome: RefusalOutcome;
  command: string;
  /** null when nothing exited: a timeout, or a command that never started. */
  exit_code: number | null;
  artifact_id: string;
}

/** Mirrors `TicketLandingStatus` in `services/landing_status.py`. */
export interface TicketLandingStatus {
  phase: LandingPhase;
  target: string;
  since: string | null;
  timeout_seconds: number | null;
  progress: string;
  progress_done: number | null;
  progress_total: number | null;
  ahead: LeaseAhead[];
  ahead_count: number;
  refused_attempts: number;
  last_refusal: RefusedAttempt | null;
}

export const ticketLandingKey = (ticketId: string) => ["ticket-landing", ticketId] as const;

export const ticketLandingApi = {
  /** What a landing ticket is waiting on before it is marked done, read from the capacity ledger. */
  ticketLanding: (id: string) => request<TicketLandingStatus>(`/api/tickets/${id}/landing`),
};
