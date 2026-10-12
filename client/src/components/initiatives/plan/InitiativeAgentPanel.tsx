import type { KeyboardEvent } from "react";

import type { ScheduleToReplace } from "../../../lib/scheduleFormat";
import { type InitiativeAgent, useInitiativeAgentStore } from "../../../state/initiativeAgentStore";
import { Button } from "../../ui/Button";
import { InitiativeBaxterPanel } from "./InitiativeBaxterPanel";
import { PlannerPanel } from "./PlannerPanel";

const AGENTS: { agent: InitiativeAgent; label: string }[] = [
  { agent: "planner", label: "Planner" },
  { agent: "baxter", label: "Baxter" },
];

/**
 * The agent docked beside the plan, and the switch between the two that work
 * on an initiative: the planner (dates, proposals, autopilot) and Baxter, the
 * triage agent (the tickets underneath). Each keeps its own conversation, so
 * switching never loses a thread; the choice is remembered across visits.
 */
export function InitiativeAgentPanel({
  initiativeId,
  canPlan,
  replaces,
  inTab = false,
}: {
  initiativeId: string;
  /** False while the initiative has no milestones: there is nothing to schedule. */
  canPlan: boolean;
  /** What a new draft would replace; null when there is no schedule yet. */
  replaces: ScheduleToReplace | null;
  /** Shown as a tab on a narrow screen rather than docked beside the plan. */
  inTab?: boolean;
}) {
  const agent = useInitiativeAgentStore((s) => s.agent);
  const setAgent = useInitiativeAgentStore((s) => s.setAgent);

  // Arrow keys move between the two tabs, as a tablist's do.
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = agent === "planner" ? "baxter" : "planner";
    setAgent(next);
    document.getElementById(`initiative-agent-tab-${next}`)?.focus();
  };

  return (
    <aside className={`plan-planner${inTab ? " plan-planner-tab" : ""}`} aria-label="Initiative agents">
      <div className="plan-mode-buttons plan-agent-switch" role="tablist" aria-label="Agent" onKeyDown={onKeyDown}>
        {AGENTS.map(({ agent: key, label }) => (
          <Button
            key={key}
            variant="plain"
            role="tab"
            id={`initiative-agent-tab-${key}`}
            aria-selected={agent === key}
            aria-controls="initiative-agent-panel"
            tabIndex={agent === key ? 0 : -1}
            className={`plan-mode-btn${agent === key ? " active" : ""}`}
            onClick={() => setAgent(key)}
          >
            {label}
          </Button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="initiative-agent-panel"
        aria-labelledby={`initiative-agent-tab-${agent}`}
        className="plan-agent-body"
      >
        {agent === "planner" ? (
          <PlannerPanel initiativeId={initiativeId} canPlan={canPlan} replaces={replaces} />
        ) : (
          <InitiativeBaxterPanel initiativeId={initiativeId} />
        )}
      </div>
    </aside>
  );
}
