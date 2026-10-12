import type { TicketState, TicketTreeNode } from "../../api/types";
import { TICKET_STATE_COLORS, TICKET_STATE_LABELS } from "../../lib/ticketStates";
import { Button } from "../ui/Button";

/**
 * Open first: these are the groups an operator acts on. Done and backlog are
 * the bulk on real data (a 93-child milestone held 38 done and 55 not started),
 * so they start collapsed behind their count rather than burying the few that
 * are moving.
 */
const GROUPS: readonly { state: TicketState; open: boolean }[] = [
  { state: "blocked", open: true },
  { state: "parked", open: true },
  { state: "in_progress", open: true },
  { state: "done", open: false },
  { state: "backlog", open: false },
  { state: "wont_do", open: false },
];

/**
 * "What has happened under this ticket, and which one do I open?"
 *
 * A milestone or feature that only groups work never runs a stage itself, so its
 * own timeline is legitimately empty — and saying "Nothing has run" there read as
 * if the work under it had not happened either. This lists the tickets under it
 * by state, each a link to its own timeline.
 */
export function TicketChildrenRollup({
  tickets,
  childCount,
  onOpenTicket,
}: {
  tickets: readonly TicketTreeNode[];
  childCount: number;
  onOpenTicket: (ticketId: string) => void;
}) {
  // The board's tree has not arrived yet, but the ticket says it has children.
  if (tickets.length === 0) {
    return (
      <div className="ap-state" role="status">
        <div className="ap-state-title">Loading the {childCount} tickets under this one…</div>
      </div>
    );
  }

  const done = tickets.filter((child) => child.state === "done").length;
  const started = tickets.filter((child) => child.state !== "backlog").length;

  return (
    <section className="tl-children" aria-label="Work under this ticket">
      <div className="tl-summary">
        <span>Nothing runs on this ticket itself; its work happens in the tickets under it.</span>
        <span>
          {done} of {tickets.length} done
          {started === 0 ? " · none started yet" : ""}
        </span>
      </div>
      {GROUPS.map(({ state, open }) => {
        const members = tickets.filter((child) => child.state === state);
        if (members.length === 0) return null;
        return (
          <details key={state} className="tl-children-group" open={open}>
            <summary>
              <span style={{ color: TICKET_STATE_COLORS[state] }}>{TICKET_STATE_LABELS[state]}</span>
              <span className="tl-children-count">{members.length}</span>
            </summary>
            <ul className="tl-children-list">
              {members.map((child) => (
                <li key={child.id}>
                  <Button variant="plain" className="tl-children-link" onClick={() => onOpenTicket(child.id)}>
                    <span className="tl-children-id">{child.external_id}</span>
                    <span className="tl-children-title">{child.title}</span>
                  </Button>
                  {child.state !== "done" && child.workflow_stage_name ? (
                    <span className="tl-children-stage">{child.workflow_stage_name}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </section>
  );
}
