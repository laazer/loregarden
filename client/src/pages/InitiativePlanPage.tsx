import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import type { InitiativePlan, MilestoneSchedule, ScheduleTargetInput } from "../api/initiativeApi";
import { InitiativeBoard } from "../components/initiatives/plan/InitiativeBoard";
import { PlannerPanel } from "../components/initiatives/plan/PlannerPanel";
import { ProposalReview } from "../components/initiatives/plan/ProposalReview";
import { ScheduleSummary } from "../components/initiatives/plan/ScheduleSummary";
import { ScheduleTable } from "../components/initiatives/plan/ScheduleTable";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { planQueryKey } from "../hooks/useInitiativePlanner";
import { navigateToPage } from "../lib/useAppNavigation";
import { ApiError } from "../api/http";
import { describeError } from "../state/toastStore";
import "../components/initiatives/plan/InitiativePlan.css";

type PlanTab = "schedule" | "board";

/** Past roughly three lines the brief is clamped, with a toggle to read it all. */
const BRIEF_PREVIEW_CHARS = 240;

const TABS: { key: PlanTab; label: string }[] = [
  { key: "schedule", label: "Schedule" },
  { key: "board", label: "Board" },
];

/**
 * One initiative, planned: when each milestone should land, when it will at the
 * current pace, the work behind it, and an agent to help set the dates.
 *
 * The question it answers is "will this initiative land when we said, and if
 * not, what moves?" — and the actions are the target fields, the order arrows,
 * and Accept on whatever the planner proposes.
 */
export function InitiativePlanPage() {
  const { initiativeId = "" } = useParams<{ initiativeId: string }>();
  const qc = useQueryClient();
  const [tab, setTab] = useState<PlanTab>("schedule");
  const [showBrief, setShowBrief] = useState(false);

  const plan = useQuery({
    queryKey: planQueryKey(initiativeId),
    queryFn: () => api.initiativePlan(initiativeId),
    // Forecasts move whenever a ticket closes anywhere under the initiative.
    refetchInterval: 60_000,
    enabled: Boolean(initiativeId),
  });

  const applyPlan = (next: InitiativePlan) => {
    qc.setQueryData(planQueryKey(initiativeId), next);
    void qc.invalidateQueries({ queryKey: ["initiatives"], exact: true });
  };

  const edit = useMutation({
    meta: { errorTitle: "Update schedule" },
    mutationFn: (body: Parameters<typeof api.updateInitiativePlan>[1]) =>
      api.updateInitiativePlan(initiativeId, body),
    onSuccess: applyPlan,
  });

  const resolve = useMutation({
    meta: { errorTitle: "Resolve proposal" },
    mutationFn: ({ proposalId, action }: { proposalId: string; action: "accept" | "discard" }) =>
      api.resolveScheduleProposal(initiativeId, proposalId, action),
    onSuccess: applyPlan,
    // A 409 means someone else resolved it first; show the plan as it is now.
    onError: () => void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) }),
  });

  const busy = edit.isPending || resolve.isPending;
  const data = plan.data;

  const setTarget = (row: MilestoneSchedule, date: string | null) =>
    edit.mutate({ targets: [{ ticket_id: row.id, target_date: date }] });

  const reorder = (ordered: MilestoneSchedule[]) =>
    edit.mutate({
      // No dates: omitted leaves each one as it is.
      targets: ordered.map<ScheduleTargetInput>((row, index) => ({ ticket_id: row.id, plan_order: index })),
    });

  const notFound = plan.error instanceof ApiError && plan.error.status === 404; // ts-org: allow-instanceof — narrowing to read the status, not formatting a message

  return (
    <div className="plan-page">
      <PageTopbar
        title={
          <span className="plan-topbar-title" title={data?.title}>
            {data ? data.title : "Initiative"}
          </span>
        }
      >
        <Button variant="secondary" compact onClick={() => navigateToPage("initiatives")}>
          ← All initiatives
        </Button>
      </PageTopbar>

      {plan.isPending ? (
        <div className="plan-main" aria-busy="true" aria-label="Loading the plan">
          <div className="plan-skeleton plan-skeleton-summary" />
          <div className="plan-skeleton plan-skeleton-table" />
        </div>
      ) : plan.isError ? (
        <div className="plan-main">
          <div className="plan-empty" role="alert">
            <p>
              {notFound
                ? "This initiative no longer exists — it may have been deleted."
                : `The plan could not be loaded: ${describeError(plan.error)}`}
            </p>
            {notFound ? (
              <Button variant="secondary" compact onClick={() => navigateToPage("initiatives")}>
                Back to initiatives
              </Button>
            ) : (
              <Button variant="secondary" compact onClick={() => void plan.refetch()}>
                Retry
              </Button>
            )}
          </div>
        </div>
      ) : data ? (
        <>
          <main className="plan-main">
            <div className="plan-heading">
              <span className="plan-mono plan-muted">{data.external_id}</span>
              {data.description ? (
                <>
                  <p id="plan-description" className={`plan-description${showBrief ? "" : " clamped"}`}>
                    {data.description}
                  </p>
                  {data.description.length > BRIEF_PREVIEW_CHARS ? (
                    <Button
                      variant="plain"
                      className="plan-more-btn"
                      aria-expanded={showBrief}
                      aria-controls="plan-description"
                      onClick={() => setShowBrief((open) => !open)}
                    >
                      {showBrief ? "Show less" : "Show the whole brief"}
                    </Button>
                  ) : null}
                </>
              ) : null}
            </div>

            <ScheduleSummary
              plan={data}
              busy={busy}
              onModeChange={(mode) => edit.mutate({ mode })}
              onTargetChange={(date) => edit.mutate({ targets: [{ ticket_id: data.id, target_date: date }] })}
            />

            {data.pending_proposal ? (
              <ProposalReview
                plan={data}
                proposal={data.pending_proposal}
                busy={busy}
                onAccept={() =>
                  data.pending_proposal &&
                  resolve.mutate({ proposalId: data.pending_proposal.id, action: "accept" })
                }
                onDiscard={() =>
                  data.pending_proposal &&
                  resolve.mutate({ proposalId: data.pending_proposal.id, action: "discard" })
                }
              />
            ) : null}

            {data.notes ? (
              <details className="plan-notes">
                <summary>Why the dates are what they are</summary>
                <p>{data.notes}</p>
              </details>
            ) : null}

            <div className="tab-bar">
              <div className="tab-bar-scroll" role="tablist" aria-label="Plan views">
                {TABS.map(({ key, label }) => (
                  <Button
                    key={key}
                    variant="plain"
                    role="tab"
                    id={`plan-tab-${key}`}
                    aria-selected={tab === key}
                    aria-controls={`plan-panel-${key}`}
                    className={`tab-btn${tab === key ? " active" : ""}`}
                    onClick={() => setTab(key)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>
            <div role="tabpanel" id={`plan-panel-${tab}`} aria-labelledby={`plan-tab-${tab}`}>
              {tab === "schedule" ? (
                <ScheduleTable plan={data} busy={busy} onReorder={reorder} onTarget={setTarget} />
              ) : (
                <InitiativeBoard initiativeId={data.id} milestones={data.milestones} />
              )}
            </div>
          </main>
          <PlannerPanel initiativeId={data.id} canPlan={data.milestones.length > 0} />
        </>
      ) : null}
    </div>
  );
}
