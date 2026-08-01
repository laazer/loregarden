/**
 * The design's primary CTA, pointed at a real endpoint.
 *
 * `POST /api/parallel/runs/{ticket_id}` has existed since parallel execution
 * shipped and nothing in the UI ever called it — the only way to put work in
 * the queue was to start it from the console. The design draws one button, but
 * dispatch needs a subject, so the button opens a picker.
 *
 * Nothing here writes local queue state: the queue socket reports what the
 * server did, and an optimistic slot that the server declined to fill would be
 * a lie the dashboard could not take back.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { API_BASE, api } from "../api/client";
import type { TicketState } from "../api/types";
import { useQueueStatus } from "../state/QueueStatusContext";

/**
 * States worth offering. Blocked work is blocked for a reason, and done /
 * wont_do have nothing left to run.
 */
const DISPATCHABLE_STATES: TicketState[] = ["backlog", "in_progress"];

export function QueueDispatchButton() {
  const { activeSlug } = useQueueStatus();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const tickets = useQuery({
    queryKey: ["queue-dispatch-tickets", activeSlug],
    queryFn: () => api.tickets({ workspace: activeSlug, state: DISPATCHABLE_STATES }),
    enabled: open && Boolean(activeSlug),
  });

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const dispatch = async (ticketId: string) => {
    setPendingId(ticketId);
    setError(null);
    try {
      // No body: the endpoint takes the workspace from the ticket, and the
      // slot count from its own default.
      const response = await fetch(`${API_BASE}/api/parallel/runs/${ticketId}`, {
        method: "POST",
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        setError(detail.detail || `Dispatch failed (${response.status})`);
        return;
      }
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dispatch failed");
    } finally {
      setPendingId(null);
    }
  };

  return (
    <div className="queue-dispatch" ref={containerRef}>
      <button
        type="button"
        className="queue-dispatch-btn"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
          <path d="m5 3 14 9-14 9z" />
        </svg>
        Dispatch run
      </button>

      {open ? (
        <div className="queue-dispatch-menu" role="dialog" aria-label="Dispatch a ticket">
          {tickets.isLoading ? (
            <div className="queue-dispatch-empty">Loading tickets…</div>
          ) : tickets.data?.length ? (
            <div className="queue-dispatch-list">
              {tickets.data.map((ticket) => (
                <button
                  key={ticket.id}
                  type="button"
                  className="queue-dispatch-item"
                  disabled={pendingId !== null}
                  onClick={() => void dispatch(ticket.id)}
                >
                  <span className="queue-dispatch-item-code">{ticket.external_id}</span>
                  <span className="queue-dispatch-item-title">{ticket.title}</span>
                  {pendingId === ticket.id ? (
                    <span className="queue-dispatch-item-state">Dispatching…</span>
                  ) : (
                    <span className="queue-dispatch-item-state">{ticket.state}</span>
                  )}
                </button>
              ))}
            </div>
          ) : (
            <div className="queue-dispatch-empty">No tickets ready to dispatch</div>
          )}

          {error ? <div className="queue-dispatch-error">{error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
