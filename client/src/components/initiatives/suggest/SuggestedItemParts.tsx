import type { SuggestedItem } from "../../../api/client";
import { TICKET_STATE_COLORS, TICKET_STATE_LABELS } from "../../../lib/ticketStates";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { Button } from "../../ui/Button";

/** What one row says about an item: where it lives, where it would leave, and how much is open. */
export function SuggestedItemText({ item }: { item: SuggestedItem }) {
  return (
    <>
      <span className="tree-state-dot" style={{ background: TICKET_STATE_COLORS[item.state] }} aria-hidden />
      <span className="initiative-pick-title">{item.title}</span>
      <span className="initiative-ws-pill">{item.workspace_slug}</span>
      <span className="initiative-muted">
        {item.from_milestone ? `leaves ${item.from_milestone}` : ""}
      </span>
      <span className="initiative-milestone-state">{TICKET_STATE_LABELS[item.state]}</span>
      <span className="initiative-muted initiative-suggest-cost">{item.cost} open</span>
    </>
  );
}

export function OpenItemButton({ item }: { item: SuggestedItem }) {
  return (
    <Button
      variant="plain"
      className="initiative-open-btn"
      aria-label={`Open ${item.external_id}`}
      title={`Open ${item.external_id}`}
      onClick={() => navigateToTicket(item.id)}
    >
      ↗
    </Button>
  );
}
