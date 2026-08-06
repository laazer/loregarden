/**
 * What the lanes already ran.
 *
 * The board only shows live entries, so a ticket that blocked mid-pipeline used
 * to vanish the moment its lane released and was only recoverable with SQL.
 * These cards are that history — one per finished entry across every workspace
 * that shares the slot pool, badged with the outcome the orchestration reported
 * rather than the queue status it exited through.
 */
import { useCallback, useEffect, useState } from "react";

import {
  listQueueHistory,
  type QueueHistoryEntry,
  type QueueHistoryOutcome,
} from "../lib/queueHistoryApi";
import { navigateToTicket } from "../lib/useAppNavigation";

const OUTCOME_FILTERS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "blocked", label: "Blocked" },
  { key: "failed", label: "Failed" },
  { key: "succeeded", label: "Succeeded" },
];

const OUTCOME_LABEL: Record<QueueHistoryOutcome, string> = {
  succeeded: "Succeeded",
  blocked: "Blocked",
  failed: "Failed",
  cancelled: "Cancelled",
  running: "Running",
  unknown: "Unknown",
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function formatWhen(entry: QueueHistoryEntry): string {
  const stamp = entry.finished_at ?? entry.started_at ?? entry.created_at;
  if (!stamp) return "";
  const parsed = new Date(stamp);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function QueueHistoryRail() {
  const [entries, setEntries] = useState<QueueHistoryEntry[]>([]);
  const [outcome, setOutcome] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listQueueHistory({ outcome, limit: 25 });
      setEntries(page.entries);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load queue history");
    } finally {
      setLoading(false);
    }
  }, [outcome]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="queue-rail-heading">Queue history</div>

      <div className="queue-history-filters tab-bar-scroll" role="tablist" aria-label="History outcomes">
        {OUTCOME_FILTERS.map((filter) => (
          <button
            key={filter.key || "all"}
            type="button"
            role="tab"
            aria-selected={outcome === filter.key}
            className={`tab-btn${outcome === filter.key ? " active" : ""}`}
            onClick={() => setOutcome(filter.key)}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {error ? <p className="queue-rail-empty">{error}</p> : null}

      {!error && entries.length === 0 ? (
        <p className="queue-rail-empty">
          {loading ? "Loading…" : "Nothing has run through a lane yet."}
        </p>
      ) : null}

      <div className="queue-history-list">
        {entries.map((entry) => (
          <button
            key={entry.entry_id}
            type="button"
            className="queue-history-card"
            onClick={() => navigateToTicket(entry.ticket_id)}
          >
            <span className="queue-history-title">{entry.ticket_title}</span>
            <span className={`queue-history-badge is-${entry.outcome}`}>
              {OUTCOME_LABEL[entry.outcome] ?? entry.outcome}
            </span>
            <span className="queue-history-meta">
              {entry.workspace_slug || entry.workspace_name} · {entry.ticket_external_id} · slot{" "}
              {entry.slot_number}
              {entry.last_stage_key ? ` · ${entry.last_stage_key}` : ""} ·{" "}
              {formatDuration(entry.duration_seconds)}
              {entry.retry_count ? ` · ${entry.retry_count} retries` : ""}
            </span>
            {entry.failure_reason ? (
              <span className="queue-history-reason">{entry.failure_reason}</span>
            ) : null}
            <span className="queue-history-when">{formatWhen(entry)}</span>
          </button>
        ))}
      </div>
    </>
  );
}
