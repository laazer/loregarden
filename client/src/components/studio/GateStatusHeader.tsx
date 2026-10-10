import type { OrchestrationProfileView } from "../../api/types";

type Status = { tone: "on" | "warn" | "off"; label: string; hint: string };

/** The saved config's real effect — `gates_enabled` is "can run", not the raw switch. */
function statusOf(profile: OrchestrationProfileView): Status {
  if (profile.gates_enabled) {
    return {
      tone: "on",
      label: "Gating transitions",
      hint: "Every stage handoff waits for these checks.",
    };
  }
  if (profile.gates_configured) {
    return {
      tone: "warn",
      label: "On, but nothing runs",
      hint: "Gates are switched on with no check and no transition script, so every handoff passes unchecked.",
    };
  }
  return { tone: "off", label: "Off", hint: "Every handoff passes without running any check." };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function GateStatusHeader({
  profile,
  enabled,
  onToggle,
  checkCount,
  fixerCount,
  agentFallback,
  maxAttempts,
}: {
  profile: OrchestrationProfileView;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  checkCount: number;
  fixerCount: number;
  agentFallback: boolean;
  maxAttempts: number;
}) {
  const status = statusOf(profile);
  const script = profile.gates_transition_script_resolved;
  const agentRetries = agentFallback && maxAttempts > 0;

  return (
    <header className="gate-header">
      <div className="gate-header-top">
        <div>
          <h2 className="gate-title">Transition gates</h2>
          <div className="gate-subtitle">{profile.name}</div>
        </div>
        <label className="gate-switch-label gate-switch-label--main">
          <input
            type="checkbox"
            className="gate-switch"
            role="switch"
            checked={enabled}
            onChange={(e) => onToggle(e.target.checked)}
          />
          Gates on
        </label>
      </div>

      <div className={`gate-status gate-status--${status.tone}`}>
        <span className="gate-status-dot" aria-hidden />
        <strong>{status.label}</strong>
        <span className="gate-status-hint">{status.hint}</span>
      </div>

      <div className={enabled ? "gate-flow" : "gate-flow gate-flow--off"} aria-label="What happens at each handoff">
        <div className="gate-flow-row">
          <span className="gate-flow-node gate-flow-node--edge">Stage finishes</span>
          <span className="gate-flow-arrow" aria-hidden>→</span>
          {script && (
            <>
              <span className="gate-flow-node" title={script}>
                Script
              </span>
              <span className="gate-flow-arrow" aria-hidden>→</span>
            </>
          )}
          <span className="gate-flow-node">{plural(checkCount, "check")}</span>
          <span className="gate-flow-arrow" aria-hidden>→</span>
          <span className="gate-flow-node gate-flow-node--pass">Next stage</span>
        </div>
        <div className="gate-flow-row gate-flow-row--fail">
          <span className="gate-flow-label">on failure</span>
          {fixerCount > 0 && (
            <>
              <span className="gate-flow-node">{plural(fixerCount, "fixer")}</span>
              <span className="gate-flow-arrow" aria-hidden>→</span>
            </>
          )}
          {agentRetries && (
            <>
              <span className="gate-flow-node">Agent retry ×{maxAttempts}</span>
              <span className="gate-flow-arrow" aria-hidden>→</span>
            </>
          )}
          <span className="gate-flow-node gate-flow-node--fail">Blocked for you</span>
        </div>
      </div>
    </header>
  );
}
