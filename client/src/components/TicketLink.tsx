import type { MouseEvent, ReactNode } from "react";
import { Link } from "react-router-dom";

import { type ArtifactTab, ticketPath } from "../lib/appNavigation";
import { peekTicket } from "../state/ticketPeekStore";

/** A click the browser should handle as a link: a new tab or window, or a download. */
function isModifiedClick(event: MouseEvent): boolean {
  return event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

/**
 * A link naming a ticket. A plain click opens the ticket's details in place
 * (`peekTicket`), with "Open ticket" there to go on; the href stays the
 * ticket's page, so a modified click or "copy link" still reaches it.
 */
export function TicketLink({
  ticketId,
  tab,
  className,
  title,
  state,
  children,
}: {
  ticketId: string;
  tab?: ArtifactTab;
  className?: string;
  title?: string;
  /** The ticket's state, as `data-state` for styles that tint by it. */
  state?: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={ticketPath(ticketId, tab)}
      className={className}
      title={title}
      data-state={state}
      onClick={(event) => {
        if (isModifiedClick(event)) return;
        event.preventDefault();
        peekTicket(ticketId, tab);
      }}
    >
      {children}
    </Link>
  );
}
