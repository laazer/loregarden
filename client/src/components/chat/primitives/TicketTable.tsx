import { Fragment, useId, useMemo, useState } from "react";

import type { TicketTreeNode } from "../../../api/types";
import { ticketStateChip } from "../../../lib/ticketStates";
import { peekTicket } from "../../../state/ticketPeekStore";
import { blockedOnStateLabel } from "../../../utils/blockKinds";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { StatusChip } from "../../ui/StatusChip";
import { TicketPriorityBadge } from "./TicketCardMeta";

/** Rows shown before the rest wait behind "Show all". */
const SHOWN = 8;
/** Past this many showing rows, a filter sits above them. */
const FILTER_FROM = 12;

function matches(node: TicketTreeNode, needle: string): boolean {
  return (
    node.title.toLowerCase().includes(needle) ||
    node.external_id.toLowerCase().includes(needle) ||
    node.children.some((child) => matches(child, needle))
  );
}

/**
 * Tickets compared across rows: priority, the ticket, its state and its stage.
 * A ticket's title opens its details in place; a ticket with tickets under it
 * opens them as indented rows beneath it. Eight rows show at first, so a card
 * answering "what's open?" over a whole workspace stays a card.
 */
export function TicketTable({ nodes, label }: { nodes: TicketTreeNode[]; label: string }) {
  const filterId = useId();
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLowerCase();
  const filtered = useMemo(() => (needle ? nodes.filter((n) => matches(n, needle)) : nodes), [nodes, needle]);
  const shown = showAll ? filtered : filtered.slice(0, SHOWN);
  const hidden = nodes.length - SHOWN;

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rows = (list: TicketTreeNode[], depth: number) =>
    list.map((node) => {
      const chip = ticketStateChip(node.state);
      const expandable = node.children.length > 0;
      const expanded = open.has(node.id);
      return (
        <Fragment key={node.id}>
          <tr>
            <td className="lg-ticket-table-p">
              <TicketPriorityBadge priority={node.priority} />
            </td>
            <td className="lg-ticket-table-ticket">
              <div className="lg-ticket-table-cell" style={depth ? { paddingLeft: depth * 16 } : undefined}>
              {expandable ? (
                <Button
                  variant="plain"
                  className="lg-ticket-table-toggle"
                  aria-expanded={expanded}
                  aria-label={`${expanded ? "Hide" : "Show"} ${node.children.length} under ${node.title}`}
                  onClick={() => toggle(node.id)}
                >
                  <span aria-hidden>{expanded ? "▾" : "▸"}</span>
                </Button>
              ) : null}
              <Button
                variant="plain"
                className="lg-ticket-table-open"
                title={`${node.external_id} — open its details`}
                onClick={() => peekTicket(node.id)}
              >
                <span className="lg-ticket-table-title">{node.title}</span>
                <span className="lg-ticket-table-id">{node.external_id}</span>
              </Button>
              </div>
            </td>
            <td className="lg-ticket-table-status">
              <StatusChip tone={chip.tone} label={blockedOnStateLabel(node.state, node.blocked_on)} />
            </td>
            <td className="lg-ticket-table-stage">{node.workflow_stage_name || "—"}</td>
          </tr>
          {expandable && expanded ? rows(node.children, depth + 1) : null}
        </Fragment>
      );
    });

  if (nodes.length === 0) {
    return <p className="lg-primitive-card-sub">No tickets here.</p>;
  }

  return (
    <div className="lg-ticket-table-wrap">
      {showAll && nodes.length > FILTER_FROM ? (
        <Input
          id={filterId}
          className="lg-ticket-table-filter"
          aria-label={`Filter ${label}`}
          placeholder="Filter by title or id"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      ) : null}
      {shown.length === 0 ? (
        <p className="lg-primitive-card-sub">No ticket matches “{filter}”.</p>
      ) : (
        <table className="lg-ticket-table" aria-label={label}>
          <thead>
            <tr>
              <th scope="col">P</th>
              <th scope="col">Ticket</th>
              <th scope="col">Status</th>
              <th scope="col">Stage</th>
            </tr>
          </thead>
          <tbody>{rows(shown, 0)}</tbody>
        </table>
      )}
      {hidden > 0 ? (
        <Button
          variant="plain"
          className="lg-ticket-table-more"
          aria-expanded={showAll}
          onClick={() => {
            setShowAll(!showAll);
            setFilter("");
          }}
        >
          {showAll ? "Show fewer" : `Show all ${nodes.length}`}
        </Button>
      ) : null}
    </div>
  );
}
