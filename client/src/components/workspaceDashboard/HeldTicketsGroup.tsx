import { useState } from "react";

import type { TicketSummary } from "../../api/types";
import { groupByRootBlocker } from "../../lib/workspaceDashboard";
import { Button } from "../ui/Button";
import { WaitingOnLinks } from "../waiting/WaitingOnLinks";

/** Roots listed before the rest fold away: past about a dozen near-identical rows, group. */
const SHOWN = 8;

/**
 * Tickets waiting on other tickets, by what they wait on first.
 *
 * Not yours to unblock, so not counted as waiting on you: the operator's
 * question is "which ticket, if it moved, would free the most?" — and whether
 * that one needs them. 322 tickets wait on a prerequisite on the live board
 * (2026-10-20); listing them would bury the section, so each row is one root
 * with how many it holds, a root needing a person first.
 */
export function HeldTicketsGroup({ held, workspaceSlug }: { held: TicketSummary[]; workspaceSlug: string }) {
  const [expanded, setExpanded] = useState(false);
  const groups = groupByRootBlocker(held);
  if (groups.length === 0) return null;
  const visible = expanded ? groups : groups.slice(0, SHOWN);

  return (
    <div className="wsd-group">
      <h3 className="wsd-group-title">
        Waiting on other tickets · {held.length} held by {groups.length}
      </h3>
      <ul className="wsd-rows">
        {visible.map(({ root, held: holding }) => (
          <li key={root.id} className="wsd-row">
            <span className="wsd-row-title">
              <WaitingOnLinks refs={[root]} here={workspaceSlug} />{" "}
              <span className="wsd-row-aside">{root.title}</span>
            </span>
            <span className="wsd-row-aside">
              holds{" "}
              <WaitingOnLinks
                refs={holding.map((ticket) => ({
                  id: ticket.id,
                  external_id: ticket.external_id,
                  title: ticket.title,
                  state: ticket.state,
                  reason: "prerequisite",
                  blocked_on: null,
                  workspace_slug: ticket.workspace_slug,
                }))}
                here={workspaceSlug}
                shown={2}
              />
            </span>
          </li>
        ))}
      </ul>
      {groups.length > SHOWN ? (
        <Button variant="plain" className="wait-more" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
          {expanded ? "Show fewer" : `Show all ${groups.length}`}
        </Button>
      ) : null}
    </div>
  );
}
