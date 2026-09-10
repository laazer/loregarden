/**
 * What the action bar's status light is allowed to claim.
 *
 * The dot used to be `background: var(--grn)` with nothing behind it — green
 * while agents ran, green while none did, green while the backend was gone.
 * An indicator that cannot be wrong is not an indicator, so the three answers
 * are modelled explicitly and "we do not know" is one of them.
 */

export type AgentPresenceState = "active" | "idle" | "unknown";

export interface AgentPresence {
  state: AgentPresenceState;
  /** Runs currently occupying a slot; 0 unless `state` is "active". */
  activeCount: number;
  /** Short human phrase for aria-label/title, always matching `state`. */
  label: string;
}

/**
 * The queue facts the light needs. `null` means no queue subscription is in
 * scope at all — the light has no source, which is "unknown", not "idle".
 */
export interface AgentPresenceInput {
  activeCount: number;
  /** Last failure from the queue socket's polling fallback, if any. */
  error: string | null;
  /** True while neither the socket nor the poll has produced a first answer. */
  loading: boolean;
}

export function deriveAgentPresence(input: AgentPresenceInput | null): AgentPresence {
  if (!input) {
    return { state: "unknown", activeCount: 0, label: "agent status unavailable" };
  }
  if (input.error) {
    return { state: "unknown", activeCount: 0, label: `backend unreachable — ${input.error}` };
  }
  if (input.loading) {
    return { state: "unknown", activeCount: 0, label: "checking agent status…" };
  }
  if (input.activeCount > 0) {
    return {
      state: "active",
      activeCount: input.activeCount,
      label: `${input.activeCount} agent${input.activeCount === 1 ? "" : "s"} running`,
    };
  }
  return { state: "idle", activeCount: 0, label: "no agents running" };
}
