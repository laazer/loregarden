import { useState } from "react";
import { api } from "../api/client";
import type { PreparedAction } from "../api/chatTypes";
import { describeError } from "../state/toastStore";
import "./PreparedActionPanel.css";

const TIER_LABELS: Record<PreparedAction["tier"], string> = {
  agent_attempted: "Agent tried it",
  one_click: "Ready to run",
  manual: "Needs a person",
};

/**
 * A human-gated block, shown as the action to take rather than a paragraph to
 * interpret.
 *
 * The ordering is deliberate. What the agent already tried comes first, because
 * that is what tells a reader whether the handover is reasonable at all — a
 * block that skipped straight to "a human must…" is the thing this exists to
 * make visible (lg-workflow-integrity-460). The command comes next, then what
 * running it will produce, then the findings if the handover was judged
 * incomplete.
 */
export function PreparedActionPanel({
  approvalId,
  action,
  findings,
  onDone,
}: {
  approvalId: string;
  action: PreparedAction;
  findings: string[];
  onDone?: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");

  const runnable = action.tier === "one_click" && Boolean(action.script_path);

  const run = async () => {
    setRunning(true);
    setError("");
    setOutput("");
    try {
      const result = await api.runHumanAction(approvalId);
      if (result.ok) {
        setOutput(result.output ?? "");
        onDone?.();
      } else {
        setError(result.error || `Exited ${result.exit_code}`);
      }
    } catch (err) {
      setError(describeError(err, "Could not run the prepared action"));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="prepared-action">
      <div className="prepared-action-tier">{TIER_LABELS[action.tier]}</div>

      {action.attempted && (
        <div className="prepared-action-row">
          <span className="prepared-action-label">Already tried</span>
          <p>{action.attempted}</p>
        </div>
      )}

      {action.prepared && (
        <div className="prepared-action-row">
          <span className="prepared-action-label">Prepared for you</span>
          <p>{action.prepared}</p>
        </div>
      )}

      {action.command && (
        <div className="prepared-action-row">
          <span className="prepared-action-label">
            {runnable ? "Runs" : "Run this"}
          </span>
          <pre className="prepared-action-command">{action.command}</pre>
        </div>
      )}

      {action.captures.length > 0 && (
        <div className="prepared-action-row">
          <span className="prepared-action-label">Captures</span>
          <ul>
            {action.captures.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {runnable && (
        <button type="button" className="btn" onClick={run} disabled={running}>
          {running ? "Running…" : "Run and capture"}
        </button>
      )}

      {output && (
        <div className="prepared-action-row">
          <span className="prepared-action-label">Captured</span>
          <pre className="prepared-action-output">{output}</pre>
        </div>
      )}

      {error && <p className="prepared-action-error">{error}</p>}

      {findings.length > 0 && (
        <div className="prepared-action-findings">
          <span className="prepared-action-label">This handover is incomplete</span>
          <ul>
            {findings.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
