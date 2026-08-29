import { useNavigate } from "react-router-dom";
import "./FinalizationConfirmation.css";

export interface FinalizationBreakdown {
  milestone?: number | string | null;
  feature?: number | string | null;
  capability?: number | string | null;
  task?: number | string | null;
  [key: string]: number | string | null | undefined;
}

export interface FinalizationResponse {
  created_ids?: unknown;
  total_created?: number;
  breakdown?: FinalizationBreakdown | null;
}

export interface FinalizationConfirmationProps {
  finalizationResponse?: FinalizationResponse | null;
  workspaceSlug?: string;
  rootHierarchyId?: string;
  isLoading?: boolean;
  error?: string | null;
  onClose?: () => void;
}

const BREAKDOWN_ORDER: Array<{ key: string; singular: string; plural: string }> = [
  { key: "milestone", singular: "milestone", plural: "milestones" },
  { key: "feature", singular: "feature", plural: "features" },
  { key: "capability", singular: "capability", plural: "capabilities" },
  { key: "task", singular: "task", plural: "tasks" },
];

function safeCount(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function FinalizationConfirmation({
  finalizationResponse,
  workspaceSlug,
  rootHierarchyId,
  isLoading = false,
  error = null,
  onClose,
}: FinalizationConfirmationProps) {
  const navigate = useNavigate();

  if (error) {
    return (
      <div >
        <h3 className="finalization-error-title">
          Unable to finalize
        </h3>
        <p className="finalization-error-message">
          {error}
        </p>
        {onClose && (
          <button type="button" className="btn-secondary" style={{ marginTop: 12 }} onClick={() => onClose()}>
            Close
          </button>
        )}
      </div>
    );
  }

  if (isLoading) {
    return (
      <div >
        <div role="status">
          Finalizing hierarchy…
        </div>
      </div>
    );
  }

  if (!finalizationResponse) {
    return null;
  }

  const createdIds = Array.isArray(finalizationResponse.created_ids) ? finalizationResponse.created_ids : [];
  const total =
    typeof finalizationResponse.total_created === "number" && Number.isFinite(finalizationResponse.total_created)
      ? finalizationResponse.total_created
      : createdIds.length;

  const breakdown =
    finalizationResponse.breakdown && typeof finalizationResponse.breakdown === "object" && !Array.isArray(finalizationResponse.breakdown)
      ? finalizationResponse.breakdown
      : null;

  const breakdownSummary = breakdown && total > 0
    ? BREAKDOWN_ORDER.map(({ key, singular, plural }) => {
        const count = safeCount(breakdown[key]);
        return `${count} ${count === 1 ? singular : plural}`;
      }).join(", ")
    : null;

  const navDisabled = !rootHierarchyId;
  const handleNavigate = () => {
    if (!rootHierarchyId) return;
    const query = workspaceSlug ? `?workspace=${encodeURIComponent(workspaceSlug)}` : "";
    navigate(`/tickets/${encodeURIComponent(rootHierarchyId)}/diff${query}`);
  };

  return (
    <div >
      <div role="status" className="finalization-header">
        <svg
          data-testid="finalization-success-icon"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--grn)"
          strokeWidth="2.5"
          aria-hidden
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
        <h3 className="finalization-title">
          Hierarchy created successfully
        </h3>
      </div>

      <div className="finalization-total">
        <span className="finalization-total-count">
          {total}
        </span>
        <span className="finalization-total-label">
          item{total === 1 ? "" : "s"}
        </span>
      </div>

      {breakdownSummary && (
        <p className="finalization-breakdown-summary">
          {breakdownSummary}
        </p>
      )}

      <div className="finalization-actions">
        <button type="button" className="btn-primary" disabled={navDisabled} onClick={handleNavigate}>
          View hierarchy
        </button>
        {onClose && (
          <button type="button" className="btn-secondary" onClick={() => onClose()}>
            Close
          </button>
        )}
      </div>
    </div>
  );
}
