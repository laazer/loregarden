import { useCIStatus } from "../hooks/useCIStatus";
import "./CIApprovalGateCheck.css";

interface CIApprovalGateCheckProps {
  ticketId: string;
}

/**
 * CI Approval Gate Check - Shows CI status within approval flow.
 * Blocks approval if CI is failing.
 */
export function CIApprovalGateCheck({ ticketId }: CIApprovalGateCheckProps) {
  const { ciStatus } = useCIStatus(ticketId);

  if (!ciStatus) return null;

  if (ciStatus.status === "passing") {
    return (
      <div className="approval-gate-check ci-check passing">
        <span className="icon">✓</span>
        <span className="label">CI Tests Passing</span>
      </div>
    );
  }

  if (ciStatus.status === "failing") {
    return (
      <div className="approval-gate-check ci-check failing">
        <span className="icon">✗</span>
        <div className="content">
          <span className="label">CI Tests Failing</span>
          {ciStatus.failure_summary && (
            <span className="detail">{ciStatus.failure_summary}</span>
          )}
        </div>
        <span className="status-indicator">Blocks approval</span>
      </div>
    );
  }

  if (ciStatus.status === "pending") {
    return (
      <div className="approval-gate-check ci-check pending">
        <span className="icon">⏳</span>
        <span className="label">CI Tests Running</span>
        <span className="status-indicator">Waiting</span>
      </div>
    );
  }

  if (ciStatus.status === "partial") {
    return (
      <div className="approval-gate-check ci-check partial">
        <span className="icon">⚠️</span>
        <div className="content">
          <span className="label">CI Partial Pass</span>
          <span className="detail">Some checks passed, some failed</span>
        </div>
        <span className="status-indicator">Review required</span>
      </div>
    );
  }

  if (ciStatus.status === "skipped") {
    return (
      <div className="approval-gate-check ci-check skipped">
        <span className="icon">⊘</span>
        <span className="label">CI Check Skipped</span>
      </div>
    );
  }

  return null;
}

/**
 * Helper to check if approval should be blocked by CI status.
 */
export function isCIBlocking(ciStatus: any): boolean {
  return ciStatus?.status === "failing" || ciStatus?.status === "pending";
}
