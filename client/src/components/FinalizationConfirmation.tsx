import { useNavigate } from "react-router-dom";

import type { TicketStudioCommitResult, WorkItemType } from "../api/client";
import { ticketPath } from "../lib/appNavigation";
import { Button } from "./ui/Button";
import "./FinalizationConfirmation.css";

export interface FinalizationConfirmationProps {
  /** What the commit created; null until it has. */
  result: Pick<TicketStudioCommitResult, "created_count" | "breakdown" | "root_ticket_id"> | null;
  isLoading: boolean;
  error: string | null;
  onClose: () => void;
}

/** Every type a commit can create. Keyed by the type, so a new one will not compile until it is listed. */
const BREAKDOWN_LABELS: Record<WorkItemType, { singular: string; plural: string }> = {
  initiative: { singular: "initiative", plural: "initiatives" },
  milestone: { singular: "milestone", plural: "milestones" },
  feature: { singular: "feature", plural: "features" },
  capability: { singular: "capability", plural: "capabilities" },
  task: { singular: "task", plural: "tasks" },
  bug: { singular: "bug", plural: "bugs" },
};

export function FinalizationConfirmation({ result, isLoading, error, onClose }: FinalizationConfirmationProps) {
  const navigate = useNavigate();

  if (error) {
    return (
      <div>
        <h3 className="finalization-error-title">Unable to finalize</h3>
        <p className="finalization-error-message">{error}</p>
        <div className="finalization-actions">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div>
        <div role="status">Finalizing hierarchy…</div>
      </div>
    );
  }

  if (!result) {
    return null;
  }

  const { created_count: total, breakdown, root_ticket_id: rootId } = result;
  const breakdownSummary =
    total > 0
      ? Object.entries(BREAKDOWN_LABELS)
          .map(([type, { singular, plural }]) => {
            const count = breakdown[type] ?? 0;
            return `${count} ${count === 1 ? singular : plural}`;
          })
          .join(", ")
      : null;

  return (
    <div>
      <div role="status" className="finalization-header">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--grn)" strokeWidth="2.5" aria-hidden>
          <path d="M20 6 9 17l-5-5" />
        </svg>
        <h3 className="finalization-title">Hierarchy created successfully</h3>
      </div>

      <div className="finalization-total">
        <span className="finalization-total-count">{total}</span>
        <span className="finalization-total-label">item{total === 1 ? "" : "s"}</span>
      </div>

      {breakdownSummary && <p className="finalization-breakdown-summary">{breakdownSummary}</p>}

      <div className="finalization-actions">
        <Button
          variant="primary"
          disabled={!rootId}
          onClick={() => {
            if (rootId) navigate(ticketPath(rootId));
          }}
        >
          View hierarchy
        </Button>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}
