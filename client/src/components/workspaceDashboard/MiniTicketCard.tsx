import { useState, type ReactNode } from "react";

import type { TicketState } from "../../api/types";
import type { ArtifactTab } from "../../lib/appNavigation";
import { Button } from "../ui/Button";
import { TicketLink } from "../TicketLink";

const STATE_LABELS: Record<TicketState, string> = {
  backlog: "Backlog",
  in_progress: "In progress",
  blocked: "Blocked",
  parked: "Parked",
  done: "Done",
  wont_do: "Won't do",
};

export interface MiniTicket {
  id: string;
  /** Shown when the title is unknown, and to screen readers after the title. */
  externalId: string;
  /** Empty when the ticket is not in the loaded tree; the card then shows the id. */
  title: string;
  state?: TicketState;
  /** The stage it sits at, or what it waits on: "Playtest", "on blob-core-132". */
  where?: string;
  /** A tag that says who has to act, drawn in the card's corner. */
  tag?: ReactNode;
}

interface MiniTicketCardProps {
  ticket: MiniTicket;
  /** Which ticket tab the card opens. */
  tab?: ArtifactTab;
  /** One line, no footer: for a ticket named inside another row (a PR's). */
  compact?: boolean;
}

/**
 * A ticket as a person recognises it: by what the work is, not by its key.
 * The id is read to a screen reader after the title, and otherwise kept out of
 * the way: twenty `blob-core-absorption-1xx` keys in a row read as noise.
 */
export function MiniTicketCard({ ticket, tab, compact = false }: MiniTicketCardProps) {
  const title = ticket.title || ticket.externalId;
  return (
    <TicketLink
      ticketId={ticket.id}
      tab={tab}
      className={compact ? "wsd-card wsd-card--compact" : "wsd-card"}
      state={ticket.state}
    >
      <span className="wsd-card-title">{title}</span>
      {ticket.title ? <span className="visually-hidden"> ({ticket.externalId})</span> : null}
      {compact ? null : (
        <span className="wsd-card-foot">
          {ticket.tag ?? (ticket.state ? <span className="wsd-card-state">{STATE_LABELS[ticket.state]}</span> : null)}
          {ticket.where ? <span className="wsd-card-where">{ticket.where}</span> : null}
        </span>
      )}
    </TicketLink>
  );
}

interface MiniTicketGridProps {
  tickets: MiniTicket[];
  /** Cards shown before "Show N more". */
  initial?: number;
  label: string;
  tab?: ArtifactTab;
}

/** A wrapping grid of cards; past `initial`, the rest wait behind one button. */
export function MiniTicketGrid({ tickets, initial = 6, label, tab }: MiniTicketGridProps) {
  const [open, setOpen] = useState(false);
  const shown = open ? tickets : tickets.slice(0, initial);
  const hidden = tickets.length - shown.length;
  return (
    <div className="wsd-cards-wrap">
      <ul className="wsd-cards" aria-label={label}>
        {shown.map((ticket) => (
          <li key={ticket.id}>
            <MiniTicketCard ticket={ticket} tab={tab} />
          </li>
        ))}
      </ul>
      {tickets.length > initial ? (
        <Button variant="plain" className="wsd-more" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Show fewer" : `Show ${hidden} more`}
        </Button>
      ) : null}
    </div>
  );
}
