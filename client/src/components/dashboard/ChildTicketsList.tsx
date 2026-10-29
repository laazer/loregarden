import { useState } from "react";

import type { TicketTreeNode } from "../../api/client";
import type { TicketState } from "../../api/types";
import { TICKET_STATE_COLORS, TICKET_STATE_LABELS } from "../../lib/ticketStates";
import { TicketTree } from "../TicketTree";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";

import "./ChildTicketsList.css";

/** Past this many children the list groups them by state, with counts, and gets a filter. */
export const GROUP_CHILDREN_AT = 12;

/** The order groups read in: what needs a person first, what is settled last. */
const STATE_ORDER: readonly TicketState[] = ["blocked", "parked", "in_progress", "backlog", "done", "wont_do"];
/** Settled work starts folded: it is the bulk of a long list and rarely what you came for. */
const RESOLVED: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);

interface ChildTicketsListProps {
  nodes: TicketTreeNode[];
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onAddChild: (parent: TicketTreeNode) => void;
}

/** The node if it, or anything under it, has `needle` in its title or id; pruned to the matching paths. */
function pruneToMatches(node: TicketTreeNode, needle: string): TicketTreeNode | null {
  const own = `${node.title} ${node.external_id}`.toLowerCase().includes(needle);
  const children = node.children
    .map((child) => pruneToMatches(child, needle))
    .filter((child): child is TicketTreeNode => child !== null);
  if (!own && children.length === 0) return null;
  return own ? node : { ...node, children };
}

/**
 * The selected ticket's children: as cards when there are a few, grouped by
 * state when there are many.
 *
 * A milestone's children answer "which of these do I open?". At a dozen, a
 * card each reads fine. At 115 (the largest milestone on the live database) a
 * card each is a wall of near-identical blocks, so past twelve they become
 * rows under a heading per state, with a count, settled work folded, and a
 * filter over title and id above them.
 */
export function ChildTicketsList({ nodes, selectedId, expandedIds, onSelect, onToggle, onAddChild }: ChildTicketsListProps) {
  const [filter, setFilter] = useState("");
  const [folded, setFolded] = useState<ReadonlySet<TicketState>>(RESOLVED);

  if (nodes.length <= GROUP_CHILDREN_AT) {
    return (
      <div className="lg-primitive-ticket-list--v6">
        <TicketTree
          nodes={nodes}
          selectedId={selectedId}
          expandedIds={expandedIds}
          onSelect={onSelect}
          onToggle={onToggle}
          onAddChild={onAddChild}
          showExternalId
          presentation="v6"
        />
      </div>
    );
  }

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? nodes.map((node) => pruneToMatches(node, needle)).filter((node): node is TicketTreeNode => node !== null)
    : nodes;
  const groups = STATE_ORDER.map((state) => ({ state, items: shown.filter((node) => node.state === state) })).filter(
    (group) => group.items.length > 0,
  );

  return (
    <div className="child-tickets">
      <Input
        type="search"
        className="child-tickets-filter"
        aria-label="Filter child tickets by title or id"
        placeholder={`Filter ${nodes.length} child tickets by title or id…`}
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && filter) {
            event.preventDefault();
            setFilter("");
          }
        }}
      />
      {groups.length === 0 ? (
        <div className="child-tickets-empty">
          <p>No child ticket&apos;s title or id contains “{filter.trim()}”.</p>
          <Button variant="secondary" compact onClick={() => setFilter("")}>
            Clear the filter
          </Button>
        </div>
      ) : (
        <div className="child-tickets-groups">
          {groups.map(({ state, items }) => (
              // A filter opens every group it matched in: a hit behind a fold
              // reads as no hit.
              <details
                key={state}
                open={Boolean(needle) || !folded.has(state)}
                onToggle={(event) => {
                  if (needle) return;
                  const open = event.currentTarget.open;
                  setFolded((was) => {
                    if (open === !was.has(state)) return was;
                    const next = new Set(was);
                    if (open) next.delete(state);
                    else next.add(state);
                    return next;
                  });
                }}
              >
                <summary className="child-tickets-summary">
                  <span className="tree-state-dot" style={{ background: TICKET_STATE_COLORS[state] }} aria-hidden />
                  <span>{TICKET_STATE_LABELS[state]}</span>
                  <span className="child-tickets-count">{items.length}</span>
                </summary>
                <TicketTree
                  nodes={items}
                  selectedId={selectedId}
                  expandedIds={expandedIds}
                  onSelect={onSelect}
                  onToggle={onToggle}
                  onAddChild={onAddChild}
                  showExternalId
                  presentation="compact"
                />
              </details>
          ))}
        </div>
      )}
    </div>
  );
}
