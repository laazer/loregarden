import { Link } from "react-router-dom";

import { pathForPage } from "../../lib/appNavigation";
import { useUiStore, type TicketDensity } from "../../state/uiStore";
import { Button } from "../ui/Button";
import "./TicketPaneViewControls.css";

const DENSITIES: { key: TicketDensity; label: string }[] = [
  { key: "compact", label: "Rows" },
  { key: "cards", label: "Cards" },
];

/**
 * The Work items pane's view controls: one line per ticket or a card, and the
 * way out to the full table when the tree is the wrong tool for finding one.
 */
export function TicketPaneViewControls() {
  const density = useUiStore((s) => s.ticketDensity);
  const setDensity = useUiStore((s) => s.setTicketDensity);
  return (
    <div className="tickets-pane-view">
      <div className="tickets-pane-density" role="group" aria-label="Ticket layout">
        {DENSITIES.map(({ key, label }) => (
          <Button
            key={key}
            variant="plain"
            className={`tickets-pane-density-btn${density === key ? " is-active" : ""}`}
            aria-pressed={density === key}
            onClick={() => setDensity(key)}
          >
            {label}
          </Button>
        ))}
      </div>
      <Link className="tickets-pane-all-link" to={pathForPage("work-items")}>
        Table view
      </Link>
    </div>
  );
}
