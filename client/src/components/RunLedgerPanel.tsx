import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { LedgerVisit } from "../api/types";
import { duration } from "../lib/duration";

const ACTIVE = new Set(["running", "queued", "awaiting_permission"]);

function VisitRow({
  visit,
  onOpenRunLog,
}: {
  visit: LedgerVisit;
  onOpenRunLog?: (runId: string) => void;
}) {
  return (
    <li style={{ padding: "7px 0", borderTop: "1px solid var(--ln)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--tx)" }}>
          {visit.stage_key}
        </span>
        {visit.visit_number > 1 && (
          // The pipeline came back here. This is the thing a flat run list hid.
          <span className="state-label" style={{ color: "var(--amb, var(--txl))" }}>
            revisit #{visit.visit_number}
          </span>
        )}
        {visit.is_parallel && <span className="state-label">{visit.attempts.length} lanes</span>}
        {!visit.is_parallel && visit.attempts.length > 1 && (
          <span className="state-label">{visit.attempts.length} attempts</span>
        )}
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--mono)",
            fontSize: 10.5,
            color: ACTIVE.has(visit.status) ? "var(--tx)" : "var(--txl)",
          }}
        >
          {visit.status}
        </span>
      </div>

      <ul style={{ margin: "3px 0 0", padding: 0, listStyle: "none" }}>
        {visit.attempts.map((attempt) => (
          <li key={attempt.run_id}>
            <button
              type="button"
              className="list-btn"
              disabled={!onOpenRunLog}
              onClick={() => onOpenRunLog?.(attempt.run_id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                fontFamily: "var(--mono)",
                fontSize: 10.5,
                color: "var(--txl)",
                padding: "2px 0",
                cursor: onOpenRunLog ? "pointer" : "default",
              }}
            >
              {attempt.agent_id}
              {attempt.skill_name ? ` · ${attempt.skill_name}` : ""} · {attempt.status}
              {attempt.duration_seconds !== null ? ` · ${duration(attempt.duration_seconds)}` : ""}
            </button>
          </li>
        ))}
      </ul>
    </li>
  );
}

/**
 * What actually happened to a ticket, in order.
 *
 * The run list showed that work occurred; it could not show the shape of it —
 * which stage each run belonged to, whether a stage was attempted more than
 * once, or whether the pipeline ever went backwards. A verify that refused and
 * sent work back to implement looked exactly like ordinary progress.
 */
export function RunLedgerPanel({
  ticketId,
  isActive,
  onOpenRunLog,
}: {
  ticketId: string;
  isActive?: boolean;
  onOpenRunLog?: (runId: string) => void;
}) {
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticketId],
    queryFn: () => api.ticketLedger(ticketId),
    refetchInterval: isActive ? 2000 : false,
  });

  if (ledger.isPending) {
    return <div style={{ padding: 16, color: "var(--txl)" }}>Loading ledger…</div>;
  }
  if (ledger.isError) {
    return <div style={{ padding: 16, color: "var(--txl)" }}>Could not load this ticket&rsquo;s ledger.</div>;
  }

  const data = ledger.data;
  if (!data || data.visits.length === 0) {
    return (
      <div style={{ padding: 40, color: "var(--txl)", textAlign: "center", fontSize: 12.5 }}>
        Nothing has run for this ticket yet.
      </div>
    );
  }

  return (
    <div style={{ padding: 16 }}>
      <div className="state-label">Ledger</div>
      <p className="modal-subtitle" style={{ margin: "2px 0 8px" }}>
        {data.total_runs} run{data.total_runs === 1 ? "" : "s"} · {duration(data.total_seconds)}
        {data.reworked_stages.length > 0 && (
          <> · reworked: {data.reworked_stages.join(", ")}</>
        )}
      </p>

      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {data.visits.map((visit) => (
          <VisitRow
            key={`${visit.stage_key}-${visit.visit_number}`}
            visit={visit}
            onOpenRunLog={onOpenRunLog}
          />
        ))}
      </ul>
    </div>
  );
}
