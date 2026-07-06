import { useState } from "react";
import { useCIStatus, useAutoFix } from "../hooks/useCIStatus";
import type { CIRunResult, AutoFixAttempt } from "../api/client";
import "./CIStatusWidget.css";

interface CIStatusWidgetProps {
  ticketId: string;
  compact?: boolean;
}

/**
 * CI Status Widget - Shows CI status badge and expandable logs panel.
 * Displays: status (passing/failing/pending), auto-fix history, logs.
 */
export function CIStatusWidget({ ticketId, compact = false }: CIStatusWidgetProps) {
  const { ciStatus, autoFixHistory } = useCIStatus(ticketId);
  const { triggerManualAutoFix, skipCICheck, isFixing, fixError } = useAutoFix(ticketId);
  const [expanded, setExpanded] = useState(false);

  if (!ciStatus) return null;

  const statusIcon = {
    passing: "✓",
    failing: "✗",
    partial: "⚠️",
    pending: "⏳",
    skipped: "⊘",
  }[ciStatus.status];

  const statusLabel = {
    passing: "Passing",
    failing: "Failing",
    partial: "Partial",
    pending: "Pending",
    skipped: "Skipped",
  }[ciStatus.status];

  const timeAgo = ciStatus.created_at
    ? new Date(ciStatus.created_at).toLocaleTimeString()
    : "unknown";

  return (
    <div className="ci-status-widget">
      <button
        className={`ci-badge ci-${ciStatus.status}`}
        onClick={() => setExpanded(!expanded)}
        title={`CI Status: ${statusLabel}`}
      >
        <span className="icon">{statusIcon}</span>
        <span className="label">CI {statusLabel}</span>
        <span className="time">{timeAgo}</span>
      </button>

      {expanded && !compact && (
        <CILogsPanel
          ciStatus={ciStatus}
          autoFixHistory={autoFixHistory}
          ticketId={ticketId}
          onAutoFix={triggerManualAutoFix}
          onSkip={skipCICheck}
          isFixing={isFixing}
          fixError={fixError}
        />
      )}
    </div>
  );
}

interface CILogsPanelProps {
  ciStatus: CIRunResult;
  autoFixHistory: AutoFixAttempt[];
  ticketId: string;
  onAutoFix: () => Promise<void>;
  onSkip: () => Promise<void>;
  isFixing: boolean;
  fixError: string | null;
}

/**
 * Expandable CI logs panel showing failure summary and auto-fix history.
 */
function CILogsPanel({
  ciStatus,
  autoFixHistory,
  ticketId,
  onAutoFix,
  onSkip,
  isFixing,
  fixError,
}: CILogsPanelProps) {
  const [showFullLogs, setShowFullLogs] = useState(false);

  return (
    <div className="ci-logs-panel">
      <div className="logs-header">
        <h4>CI Details</h4>
        <div className="header-actions">
          {ciStatus.logs_url && (
            <a href={ciStatus.logs_url} target="_blank" rel="noopener noreferrer" className="link">
              Full logs →
            </a>
          )}
          {!showFullLogs && ciStatus.full_logs && (
            <button
              className="toggle-btn"
              onClick={() => setShowFullLogs(true)}
            >
              Show logs
            </button>
          )}
        </div>
      </div>

      {ciStatus.failure_summary && (
        <div className="failure-summary">
          <strong>Error:</strong> {ciStatus.failure_summary}
        </div>
      )}

      {showFullLogs && ciStatus.full_logs && (
        <div className="logs-content">
          <pre>{ciStatus.full_logs.slice(-2000)}</pre>
        </div>
      )}

      {/* Auto-fix section */}
      {ciStatus.status === "failing" && (
        <div className="auto-fix-section">
          <h5>Auto-Fix</h5>

          {autoFixHistory.length > 0 && (
            <div className="fix-attempts">
              {autoFixHistory.map((attempt) => (
                <div key={attempt.id} className="fix-attempt">
                  <span className="attempt-num">Attempt {attempt.attempt_number}</span>
                  <span className={`attempt-status status-${attempt.status}`}>
                    {attempt.status}
                  </span>
                  {attempt.result_summary && (
                    <span className="attempt-result">{attempt.result_summary}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="fix-actions">
            <button
              className="retry-btn"
              onClick={onAutoFix}
              disabled={isFixing}
            >
              {isFixing ? "Triggering..." : "Retry Auto-Fix"}
            </button>
            <button
              className="skip-btn"
              onClick={onSkip}
              disabled={isFixing}
              title="Admin: Skip CI gate and proceed"
            >
              Skip CI Check
            </button>
          </div>

          {fixError && <div className="error-message">{fixError}</div>}
        </div>
      )}

      {ciStatus.status === "passing" && (
        <div className="success-message">✓ All checks passed</div>
      )}

      {ciStatus.status === "skipped" && (
        <div className="skipped-message">⊘ CI check skipped</div>
      )}
    </div>
  );
}
