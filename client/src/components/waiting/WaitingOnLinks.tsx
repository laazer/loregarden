import { Fragment, useId, useState } from "react";

import type { WaitingOnRef } from "../../api/types";
import { waitingOnLabel, waitingOnStatus } from "../../utils/blockKinds";
import { TicketNameLink } from "../chat/primitives/ResourceActionButton";
import { Button } from "../ui/Button";
import "./WaitingOn.css";

/**
 * The tickets one waits on, each a link to it.
 *
 * Every surface that said "Waiting on lg-x +2" did it as plain text — the board
 * tile, the Needs-you card, the tree — so the one thing the line named could not
 * be opened from it. A ticket waits on 1.9 prerequisites on average and on 18 at
 * most (live, 2026-10-20), so past `shown` the rest sit behind a disclosure
 * rather than wrapping a tile.
 *
 * Clicks stop here: the links sit inside rows and tiles that open their own
 * ticket on click, and a click on the blocker must open the blocker. Each name
 * goes through `TicketNameLink`, so in a Flex View pane (nowhere to navigate)
 * it is text.
 */
export function WaitingOnLinks({
  refs,
  here,
  shown = 3,
  className = "",
}: {
  refs: WaitingOnRef[];
  /** The workspace being looked at; a blocker elsewhere is prefixed with its own. */
  here?: string;
  shown?: number;
  className?: string;
}) {
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  if (refs.length === 0) return null;
  const visible = expanded ? refs : refs.slice(0, shown);
  const hidden = refs.length - visible.length;

  return (
    <span id={listId} className={`wait-links ${className}`.trim()}>
      {/* A space after each, for the accessible text; the gap draws the visual one. */}
      {visible.map((ref) => (
        <Fragment key={ref.id}>
          <TicketNameLink
            ticketId={ref.id}
            className={`wait-link${ref.blocked_on === "person" ? " wait-link--person" : ""}`}
            title={`${ref.title} — ${waitingOnStatus(ref)}`}
          >
            {waitingOnLabel(ref, here)}
          </TicketNameLink>{" "}
        </Fragment>
      ))}
      {hidden > 0 || expanded ? (
        <Button
          variant="plain"
          className="wait-more"
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((open) => !open);
          }}
        >
          {expanded ? "fewer" : `+${hidden} more`}
        </Button>
      ) : null}
    </span>
  );
}
