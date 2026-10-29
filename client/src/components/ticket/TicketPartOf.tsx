import { Link } from "react-router-dom";

import type { TicketTreeNode } from "../../api/client";
import { ticketPath, type ArtifactTab } from "../../lib/appNavigation";
import { partStanding } from "../../lib/ticketParts";

interface TicketPartOfProps {
  ticketId: string;
  parent: { id: string; title: string };
  siblings: readonly TicketTreeNode[];
  tab: ArtifactTab;
}

/**
 * On one part of a split ticket: which ticket it is part of, and where it sits
 * among its siblings, with the parts either side of it one link away.
 */
export function TicketPartOf({ ticketId, parent, siblings, tab }: TicketPartOfProps) {
  const at = siblings.findIndex((s) => s.id === ticketId);
  if (at < 0) return null;
  const previous = siblings[at - 1];
  const next = siblings[at + 1];
  return (
    <div className="ticket-part-of">
      <span>
        Part {at + 1} of {siblings.length} of{" "}
        <Link to={ticketPath(parent.id, tab)} className="ticket-part-of-parent">
          {parent.title}
        </Link>
      </span>
      <span className="ticket-part-of-segments" aria-hidden>
        {siblings.map((s) => (
          <i key={s.id} className={s.id === ticketId ? "is-current" : `is-${partStanding(s)}`} />
        ))}
      </span>
      {previous ? (
        <Link to={ticketPath(previous.id, tab)} title={previous.title}>
          Previous part
        </Link>
      ) : null}
      {next ? (
        <Link to={ticketPath(next.id, tab)} title={next.title}>
          Next part
        </Link>
      ) : null}
    </div>
  );
}
