import { useState } from "react";

import type { NodeStatus, PlanNode } from "../../../api/initiativeApi";
import type { TicketSummary } from "../../../api/types";
import { ticketStateLabel } from "../../../lib/ticketStates";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { Button } from "../../ui/Button";

/** Children drawn per level before "Show all", as the board caps its columns: a phase can hold eighty tickets. */
const SHOWN = 12;

/** What the plan's graph adds to a ticket's state, when it says something the state does not. */
const NODE_HINT: Partial<Record<NodeStatus, string>> = {
  ready: "ready to start",
  waiting: "waiting on prerequisites",
  needs_person: "for a person",
};

interface TreeContext {
  childrenOf: Map<string, TicketSummary[]>;
  nodeById: Map<string, PlanNode>;
}

function Branch({ items, ctx, above }: { items: TicketSummary[]; ctx: TreeContext; above: ReadonlySet<string> }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      {shown.map((ticket) => (
        <TreeItem key={ticket.id} ticket={ticket} ctx={ctx} above={above} />
      ))}
      {items.length > shown.length ? (
        <li>
          <Button variant="plain" className="plan-inline-btn" onClick={() => setAll(true)}>
            Show all {items.length}
          </Button>
        </li>
      ) : null}
    </>
  );
}

function TreeItem({ ticket, ctx, above }: { ticket: TicketSummary; ctx: TreeContext; above: ReadonlySet<string> }) {
  const [open, setOpen] = useState(false);
  // A parent loop would otherwise unfold forever.
  const kids = (ctx.childrenOf.get(ticket.id) ?? []).filter((kid) => !above.has(kid.id));
  const hint = NODE_HINT[ctx.nodeById.get(ticket.id)?.status ?? "done"];
  const resolved = ticket.state === "done" || ticket.state === "wont_do";
  const listId = `plan-tree-${ticket.id}`;
  return (
    <li className={resolved ? "plan-row-done" : undefined}>
      <div className="plan-tree-item">
        {kids.length > 0 ? (
          <Button
            variant="plain"
            className="plan-icon-btn plan-tree-toggle"
            aria-expanded={open}
            aria-controls={listId}
            aria-label={`${open ? "Hide" : "Show"} the ${kids.length} under ${ticket.external_id}`}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "▾" : "▸"}
          </Button>
        ) : (
          <span className="plan-tree-spacer" aria-hidden />
        )}
        <Button
          variant="plain"
          className="plan-link"
          title={`Open ${ticket.external_id}`}
          onClick={() => navigateToTicket(ticket.id)}
        >
          <span className="plan-mono">{ticket.external_id}</span> {ticket.title}
        </Button>
        <span className="plan-muted plan-tree-meta">
          {ticket.work_item_type} · {ticketStateLabel(ticket.state)}
          {hint && !resolved ? ` · ${hint}` : ""}
          {kids.length > 0 ? ` · ${kids.length} under it` : ""}
        </span>
      </div>
      {open && kids.length > 0 ? (
        <ul className="plan-tree" id={listId}>
          <Branch items={kids} ctx={ctx} above={new Set(above).add(ticket.id)} />
        </ul>
      ) : null}
    </li>
  );
}

/**
 * The work under one phase, as the tree it is: features under a milestone,
 * tasks under a feature. Each level opens on demand, so a phase of eighty
 * tickets reads as its handful of features first.
 */
export function PhaseTree({
  root,
  childrenOf,
  nodeById,
  id,
}: {
  root: { id: string; external_id: string };
  childrenOf: Map<string, TicketSummary[]>;
  nodeById: Map<string, PlanNode>;
  id: string;
}) {
  const kids = childrenOf.get(root.id) ?? [];
  if (kids.length === 0) {
    return (
      <p className="plan-muted" id={id}>
        Nothing under {root.external_id} yet.
      </p>
    );
  }
  return (
    <ul className="plan-tree plan-tree-root" id={id} aria-label={`Work under ${root.external_id}`}>
      <Branch items={kids} ctx={{ childrenOf, nodeById }} above={new Set([root.id])} />
    </ul>
  );
}
