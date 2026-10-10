import { useState } from "react";

import { useInitiativePlanner } from "../../../hooks/useInitiativePlanner";
import type { ScheduleToReplace } from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { StudioChatComposer, StudioChatMessages } from "../../studio/StudioChat";
import { RedraftConfirmModal } from "./RedraftConfirmModal";

const PLANNER_LABEL = "Planner";

/**
 * The planner conversation, docked beside the schedule.
 *
 * Answers "what should the dates be, and what would it take to hit one?". Its
 * replies end in a proposal the schedule shows for acceptance, so the action
 * this leads to is the Accept button on the left, not anything in here.
 */
export function PlannerPanel({
  initiativeId,
  canPlan,
  replaces,
  inTab = false,
}: {
  initiativeId: string;
  /** False while the initiative has no milestones: there is nothing to schedule. */
  canPlan: boolean;
  /** What a new draft would replace; null when there is no schedule yet, and drafting goes straight through. */
  replaces: ScheduleToReplace | null;
  /** Shown as a tab on a narrow screen rather than docked beside the plan. */
  inTab?: boolean;
}) {
  const planner = useInitiativePlanner(initiativeId);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);

  const submit = () => {
    const content = draft.trim();
    if (!content) return;
    setDraft("");
    planner.send(content, () => setDraft(content));
  };

  return (
    <aside className={`plan-planner${inTab ? " plan-planner-tab" : ""}`} aria-label="Planning agent">
      <header className="plan-planner-head">
        <div>
          <h2 className="plan-section-title">Planning agent</h2>
          <p className="plan-muted">
            Proposes dates you accept, marks work for a person, and can start work or run the autopilot.
          </p>
        </div>
        <Button
          variant="primary"
          compact
          disabled={!canPlan || planner.isBusy}
          title={canPlan ? undefined : "Attach a milestone to this initiative first"}
          onClick={() => (replaces ? setConfirming(true) : planner.draft())}
        >
          {planner.isBusy ? "Planning…" : "Draft schedule"}
        </Button>
      </header>

      <div className="plan-planner-thread">
        {planner.loadError ? (
          <div className="plan-empty" role="alert">
            <p>{planner.loadError}</p>
            <Button variant="secondary" compact onClick={planner.retry}>
              Retry
            </Button>
          </div>
        ) : planner.isLoading ? (
          <div className="plan-skeleton plan-skeleton-thread" aria-busy="true" aria-label="Loading conversation" />
        ) : (
          <StudioChatMessages
            messages={planner.messages}
            isThinking={planner.isBusy}
            activeTurnId={planner.activeTurnId}
            thinkingMessage="Planner is working…"
            thinkingSub="Reading the plan and its pace…"
            assistantLabel={PLANNER_LABEL}
            // The avatar is Baxter's; the planner is a different agent.
            showAssistantAvatar={false}
            emptyMessage={
              canPlan
                ? "Draft a schedule to start, or ask something like “what would it take to land the beta by Nov 15?”"
                : "Attach a milestone to this initiative, then the planner can schedule it."
            }
          />
        )}
      </div>

      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submit}
        onStop={planner.stop}
        isSending={planner.isBusy}
        isStopping={planner.isStopping}
        disabled={!canPlan}
        placeholder={canPlan ? "Ask the planner…" : "Attach a milestone first"}
        error={planner.sendError}
        dense
      />

      <RedraftConfirmModal
        replaces={confirming ? replaces : null}
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          planner.draft();
        }}
      />
    </aside>
  );
}
