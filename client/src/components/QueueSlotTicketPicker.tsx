/**
 * The picker that fills an empty slot.
 *
 * This replaces the old "Dispatch run" button, which sat above the board and
 * launched a ticket into whichever slot the server happened to pick. The board
 * already draws slots as lanes, so the lane is the affordance: pick a ticket
 * into *this* slot, then start it. Picking stages; it does not launch.
 *
 * Tickets come from every workspace, unfiltered. The slots are one shared pool,
 * so scoping the picker to a workspace would let you fill the machine from one
 * project while pretending the others were not competing for it.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type { TicketState } from "../api/types";
import { useQueueStatus } from "../state/QueueStatusContext";

/**
 * States worth offering. Blocked work is blocked for a reason, and done /
 * wont_do have nothing left to run.
 */
const DISPATCHABLE_STATES: TicketState[] = ["backlog", "in_progress"];

interface QueueSlotTicketPickerProps {
  slotNumber: number;
  /** Ticket ids already staged in other slots — offering them twice is a trap. */
  excludedTicketIds: string[];
  onPick: (ticket: {
    ticketId: string;
    code: string;
    title: string;
    workspaceName: string;
  }) => void;
}

export function QueueSlotTicketPicker({
  slotNumber,
  excludedTicketIds,
  onPick,
}: QueueSlotTicketPickerProps) {
  const { workspaces } = useQueueStatus();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const tickets = useQuery({
    // No workspace filter: one pool, one pick list.
    queryKey: ["queue-slot-tickets"],
    queryFn: () => api.tickets({ state: DISPATCHABLE_STATES }),
    enabled: open,
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

  useEffect(() => {
    if (open) searchRef.current?.focus();
    else setSearch("");
  }, [open]);

  const workspaceNameBySlug = useMemo(
    () => new Map(workspaces.map((ws) => [ws.slug, ws.name])),
    [workspaces],
  );

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (tickets.data ?? [])
      .filter((ticket) => !excludedTicketIds.includes(ticket.id))
      .map((ticket) => ({
        ticket,
        workspaceName: workspaceNameBySlug.get(ticket.workspace_slug ?? "") ?? "",
      }))
      .filter(
        ({ ticket, workspaceName }) =>
          !query ||
          ticket.title.toLowerCase().includes(query) ||
          (ticket.external_id ?? "").toLowerCase().includes(query) ||
          // Searchable by workspace, since the list spans all of them.
          workspaceName.toLowerCase().includes(query),
      );
  }, [tickets.data, excludedTicketIds, search, workspaceNameBySlug]);

  return (
    <div className="queue-slot-picker" ref={containerRef}>
      <button
        type="button"
        className="queue-slot-picker-btn"
        aria-expanded={open}
        aria-label={`Add a ticket to slot ${slotNumber}`}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden>
          <path d="M12 5v14M5 12h14" />
        </svg>
        Add ticket
      </button>

      {open ? (
        <div className="queue-dispatch-menu" role="dialog" aria-label={`Add a ticket to slot ${slotNumber}`}>
          <input
            ref={searchRef}
            type="search"
            className="queue-dispatch-search"
            placeholder="Search tickets…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />

          {tickets.isLoading ? (
            <div className="queue-dispatch-empty">Loading tickets…</div>
          ) : visible.length ? (
            <div className="queue-dispatch-list">
              {visible.map(({ ticket, workspaceName }) => (
                <button
                  key={ticket.id}
                  type="button"
                  className="queue-dispatch-item"
                  onClick={() => {
                    onPick({
                      ticketId: ticket.id,
                      code: ticket.external_id ?? "",
                      title: ticket.title,
                      workspaceName,
                    });
                    setOpen(false);
                  }}
                >
                  <span className="queue-dispatch-item-code">{ticket.external_id}</span>
                  <span className="queue-dispatch-item-title">{ticket.title}</span>
                  <span className="queue-dispatch-item-state">
                    {workspaceName || ticket.state}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="queue-dispatch-empty">
              {search.trim() ? "No tickets match" : "No tickets ready to run"}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
