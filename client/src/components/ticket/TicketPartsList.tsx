import type { TicketTreeNode } from "../../api/client";
import { partStanding, type PartStanding } from "../../lib/ticketParts";
import { ticketStateChip } from "../../lib/ticketStates";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

interface TicketPartsListProps {
  heading: string;
  parts: readonly TicketTreeNode[];
  onOpenTicket: (id: string) => void;
}

/** Said in words beside the vine's mark, so the standing never rests on colour or shape. */
const STANDING_TEXT: Record<PartStanding, string> = {
  done: "done",
  running: "running",
  blocked: "blocked",
  waiting: "waiting",
};

/**
 * A split ticket's parts, in the order they run, on the vine the plan card
 * uses: a leaf when done, a ringed bud while running, a closed bud for the
 * rest. One link per part, on its name.
 */
export function TicketPartsList({ heading, parts, onOpenTicket }: TicketPartsListProps) {
  return (
    <section className="ticket-parts" aria-label={heading}>
      <div className="state-label workflow-lifecycle-label ticket-workflow-heading">
        {heading} <span className="ticket-parts-count">{parts.length}</span>
      </div>
      <ol className="ticket-parts-list">
        {parts.map((part) => {
          const standing = partStanding(part);
          const chip = ticketStateChip(part.state);
          const where =
            standing === "waiting" && part.waiting_on?.length
              ? `after ${part.waiting_on.map((w) => w.external_id).join(", ")}`
              : part.workflow_stage_name;
          return (
            <li key={part.id} className={`ticket-part is-${standing}`}>
              <span className="ticket-part-mark" aria-hidden />
              <div className="ticket-part-body">
                <Button variant="plain" className="ticket-part-name" onClick={() => onOpenTicket(part.id)}>
                  {part.title}
                  <span className="ticket-sr-only">, {STANDING_TEXT[standing]}</span>
                </Button>
                <div className="ticket-part-ref">
                  {part.external_id}
                  {where ? ` · ${where}` : ""}
                </div>
              </div>
              <StatusChip tone={chip.tone} label={chip.label} />
            </li>
          );
        })}
      </ol>
    </section>
  );
}
