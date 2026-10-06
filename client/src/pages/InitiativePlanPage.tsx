import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import { ApiError } from "../api/http";
import type { InitiativePlan, MilestoneSchedule, ScheduleTargetInput } from "../api/initiativeApi";
import { AutopilotPanel } from "../components/initiatives/plan/AutopilotPanel";
import { InitiativeBoard } from "../components/initiatives/plan/InitiativeBoard";
import { PlannerPanel } from "../components/initiatives/plan/PlannerPanel";
import { ProposalReview } from "../components/initiatives/plan/ProposalReview";
import { ScheduleSummary } from "../components/initiatives/plan/ScheduleSummary";
import { ScheduleTable } from "../components/initiatives/plan/ScheduleTable";
import { TrackedTickets } from "../components/initiatives/plan/TrackedTickets";
import { MarkdownContent } from "../components/chat/MarkdownContent";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { planQueryKey } from "../hooks/useInitiativePlanner";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { navigateToPage } from "../lib/useAppNavigation";
import { describeError } from "../state/toastStore";
import "../components/initiatives/plan/InitiativePlan.css";

type PlanTab = "schedule" | "board" | "planner";

/** Past roughly three lines the brief is clamped, with a toggle to read it all. */
const BRIEF_PREVIEW_CHARS = 240;

/** Below this the planner stops being docked and becomes a tab — the same breakpoint as the layout. */
const NARROW = "(max-width: 1100px)";

const TAB_LABEL: Record<PlanTab, string> = {
  schedule: "Schedule",
  board: "Board",
  planner: "Planner",
};

/**
 * One initiative, planned and driven: when each phase should land, when it
 * will, the work behind it, what is waiting on a person, and an agent — or the
 * autopilot — to start the work.
 *
 * The question it answers is "will this initiative land when we said, and what
 * moves it?" Every action here is also an MCP tool, so the planner agent can do
 * what the page does: set targets and order, accept proposals, mark work for a
 * person, start ready work, run the autopilot.
 */
export function InitiativePlanPage() {
  const { initiativeId = "" } = useParams<{ initiativeId: string }>();
  const qc = useQueryClient();
  const narrow = useMediaQuery(NARROW);
  const [tab, setTab] = useState<PlanTab>("schedule");
  const [showBrief, setShowBrief] = useState(false);
  const tabs: PlanTab[] = narrow ? ["schedule", "board", "planner"] : ["schedule", "board"];
  const activeTab = tabs.includes(tab) ? tab : "schedule";

  const plan = useQuery({
    queryKey: planQueryKey(initiativeId),
    queryFn: () => api.initiativePlan(initiativeId),
    // Forecasts and readiness move whenever a ticket closes anywhere under it.
    refetchInterval: 30_000,
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

  const autopilot = useMutation({
    meta: { errorTitle: "Change the autopilot" },
    mutationFn: (change: { enabled?: boolean; max_parallel?: number }) => api.setAutopilot(initiativeId, change),
    onSuccess: applyPlan,
  });

  const needsPerson = useMutation({
    meta: { errorTitle: "Mark work for a person" },
    mutationFn: ({ ids, value }: { ids: string[]; value: boolean }) =>
      api.markNeedsPerson(initiativeId, ids, value),
    onSuccess: applyPlan,
  });

  const startWork = useMutation({
    meta: { errorTitle: "Start work" },
    mutationFn: (ids: string[]) => api.startInitiativeWork(initiativeId, ids),
    onSettled: () => void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) }),
  });

  const busy = edit.isPending || resolve.isPending || autopilot.isPending || needsPerson.isPending;
  const data = plan.data;

  const setTarget = (row: MilestoneSchedule, date: string | null) =>
    edit.mutateAsync({ targets: [{ ticket_id: row.id, target_date: date }] });

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
                  <div id="plan-description" className={`plan-description${showBrief ? "" : " clamped"}`}>
                    <MarkdownContent content={data.description} expandable={false} />
                  </div>
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
              onTargetChange={(date) => edit.mutateAsync({ targets: [{ ticket_id: data.id, target_date: date }] })}
            />

            {data.pending_proposal ? (
              <ProposalReview
                plan={data}
                proposal={data.pending_proposal}
                busy={busy}
                onAccept={() =>
                  data.pending_proposal && resolve.mutate({ proposalId: data.pending_proposal.id, action: "accept" })
                }
                onDiscard={() =>
                  data.pending_proposal && resolve.mutate({ proposalId: data.pending_proposal.id, action: "discard" })
                }
              />
            ) : null}

            <TrackedTickets
              initiativeId={data.id}
              onChanged={() => {
                void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) });
                void qc.invalidateQueries({ queryKey: ["initiatives"], exact: true });
              }}
            />

            <AutopilotPanel
              plan={data}
              busy={busy}
              onAutopilot={(change) => autopilot.mutate(change)}
              onNeedsPerson={(ids, value) => needsPerson.mutate({ ids, value })}
            />

            {data.notes ? (
              <details className="plan-notes">
                <summary>Why the dates are what they are</summary>
                <p>{data.notes}</p>
              </details>
            ) : null}

            <div className="tab-bar">
              <div className="tab-bar-scroll" role="tablist" aria-label="Plan views">
                {tabs.map((key) => (
                  <Button
                    key={key}
                    variant="plain"
                    role="tab"
                    id={`plan-tab-${key}`}
                    aria-selected={activeTab === key}
                    aria-controls={`plan-panel-${key}`}
                    className={`tab-btn${activeTab === key ? " active" : ""}`}
                    onClick={() => setTab(key)}
                  >
                    {TAB_LABEL[key]}
                  </Button>
                ))}
              </div>
            </div>
            <div role="tabpanel" id={`plan-panel-${activeTab}`} aria-labelledby={`plan-tab-${activeTab}`}>
              {activeTab === "schedule" ? (
                <ScheduleTable plan={data} busy={busy} onReorder={reorder} onTarget={setTarget} />
              ) : activeTab === "board" ? (
                <InitiativeBoard
                  initiativeId={data.id}
                  milestones={data.milestones}
                  nodes={data.nodes}
                  onNeedsPerson={(ids, value) => needsPerson.mutateAsync({ ids, value })}
                  onStart={(ids) => startWork.mutateAsync(ids)}
                />
              ) : (
                <PlannerPanel initiativeId={data.id} canPlan={data.milestones.length > 0} inTab />
              )}
            </div>
          </main>
          {narrow ? null : <PlannerPanel initiativeId={data.id} canPlan={data.milestones.length > 0} />}
        </>
      ) : null}
    </div>
  );
}
