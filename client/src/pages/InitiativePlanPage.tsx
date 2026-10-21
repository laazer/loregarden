import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import { ApiError } from "../api/http";
import type { InitiativePlan, MilestoneSchedule, ScheduleTargetInput } from "../api/initiativeApi";
import { InitiativeAutopilot } from "../components/initiatives/plan/InitiativeAutopilot";
import { InitiativeBoard } from "../components/initiatives/plan/InitiativeBoard";
import { InitiativeGantt } from "../components/initiatives/plan/InitiativeGantt";
import { ProposalReview } from "../components/initiatives/plan/ProposalReview";
import { RedraftConfirmModal } from "../components/initiatives/plan/RedraftConfirmModal";
import { ScheduleSummary } from "../components/initiatives/plan/ScheduleSummary";
import { ScheduleTable } from "../components/initiatives/plan/ScheduleTable";
import { TrackTicketSearch } from "../components/initiatives/plan/TrackTicketSearch";
import { MarkdownContent } from "../components/chat/MarkdownContent";
import { PageTopbar } from "../components/TopbarPageSlot";
import { TicketRouteResolver } from "../components/TicketRouteResolver";
import { Button } from "../components/ui/Button";
import { planQueryKey, useInitiativePlanner } from "../hooks/useInitiativePlanner";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { boardQueryKey } from "../lib/initiativeBoard";
import { navigateToPage, ticketUuidForRef } from "../lib/useAppNavigation";
import { scheduleToReplace } from "../lib/scheduleFormat";
import { useTicketRefStore } from "../state/ticketRefStore";
import { describeError } from "../state/toastStore";
import { useUiStore } from "../state/uiStore";
import "../components/initiatives/plan/InitiativePlan.css";

type PlanTab = "schedule" | "gantt" | "board";

/** Past roughly three lines the brief is clamped, with a toggle to read it all. */
const BRIEF_PREVIEW_CHARS = 240;

/** Below this the omnibar stays where the operator put it: a right column would leave the plan no room. */
const NARROW = "(max-width: 1100px)";

const TAB_LABEL: Record<PlanTab, string> = {
  schedule: "Schedule",
  gantt: "Gantt",
  board: "Board",
};

const TABS: PlanTab[] = ["schedule", "gantt", "board"];

/**
 * `/initiatives/:initiativeId`, addressed by the initiative's shareable id
 * (`init-always-on-2`) the way a ticket is. An initiative is a ticket, so
 * `TicketRouteResolver` learns the id pair and swaps a UUID address for the
 * readable one; the page below is keyed by the UUID only, because its child
 * panels invalidate the plan under `data.id`.
 */
export function InitiativePlanRoute() {
  const { initiativeId = "" } = useParams<{ initiativeId: string }>();
  const uuid = useTicketRefStore((s) => ticketUuidForRef(initiativeId, s.uuidByRef));
  return (
    <TicketRouteResolver param="initiativeId" basePath="/initiatives">
      {uuid ? <InitiativePlanPage initiativeId={uuid} /> : null}
    </TicketRouteResolver>
  );
}

/**
 * One initiative, planned and driven: when each phase should land, when it
 * will, the work behind it, what is waiting on a person, and an agent — or the
 * autopilot — to start the work.
 *
 * The question it answers is "will this initiative land when we said, and what
 * moves it?" Every action here is also an MCP tool, so the planner agent can do
 * what the page does: set targets and order, accept proposals, mark work for a
 * person, start ready work, run the autopilot.
 *
 * Its agents (the planner, and Baxter on the work underneath) talk in the
 * omnibar, which this page opens docked on the right: one chat surface, not a
 * second one beside the schedule.
 */
export function InitiativePlanPage({ initiativeId }: { initiativeId: string }) {
  const qc = useQueryClient();
  const narrow = useMediaQuery(NARROW);
  const [tab, setTab] = useState<PlanTab>("schedule");
  const [showBrief, setShowBrief] = useState(false);
  const [confirmingDraft, setConfirmingDraft] = useState(false);
  // Same query key the omnibar binds, so the Draft button costs no request.
  const planner = useInitiativePlanner(initiativeId);

  // The omnibar is this page's agent panel: open it on arrival, on the right
  // where the panel was. The edge is the screen's, not the operator's saved
  // one, so leaving gives theirs back; choosing an edge here overrides it.
  const setCopilotOpen = useUiStore((s) => s.setCopilotOpen);
  const setScreenDockEdge = useUiStore((s) => s.setScreenDockEdge);
  useEffect(() => {
    setCopilotOpen(true);
  }, [initiativeId, setCopilotOpen]);
  useEffect(() => {
    if (narrow) return undefined;
    setScreenDockEdge("right");
    return () => setScreenDockEdge(null);
  }, [narrow, setScreenDockEdge]);

  const plan = useQuery({
    queryKey: planQueryKey(initiativeId),
    queryFn: () => api.initiativePlan(initiativeId),
    // Forecasts and readiness move whenever a ticket closes anywhere under it.
    refetchInterval: 30_000,
    enabled: Boolean(initiativeId),
  });

  // Where each tracked ticket stays: the schedule names it on the ticket's row.
  const view = useQuery({
    queryKey: ["initiatives", initiativeId, "view"],
    queryFn: () => api.initiative(initiativeId),
    enabled: Boolean(initiativeId),
  });
  const homes = useMemo(
    () => new Map((view.data?.milestones ?? []).filter((m) => m.home_milestone).map((m) => [m.id, m.home_milestone])),
    [view.data],
  );

  /** Tracking changed: the phases, the tree under them and the list counts all move. */
  const membershipChanged = () => {
    void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) });
    void qc.invalidateQueries({ queryKey: ["initiatives", initiativeId, "view"] });
    void qc.invalidateQueries({ queryKey: boardQueryKey(initiativeId) });
    void qc.invalidateQueries({ queryKey: ["initiatives"], exact: true });
  };

  const untrack = useMutation({
    meta: { errorTitle: "Remove a ticket from this initiative" },
    mutationFn: (ticketId: string) => api.removeInitiativeMember(initiativeId, ticketId),
    onSuccess: membershipChanged,
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

  const busy = edit.isPending || resolve.isPending || autopilot.isPending || needsPerson.isPending || untrack.isPending;
  const data = plan.data;

  const draftSchedule = () => {
    if (!data) return;
    // Open the thread too: the planner's reply explains the proposal it drafts.
    setCopilotOpen(true);
    if (scheduleToReplace(data)) setConfirmingDraft(true);
    else planner.draft();
  };

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
        leading={
          <Button variant="secondary" compact onClick={() => navigateToPage("initiatives")}>
            ← All initiatives
          </Button>
        }
        title={
          <span className="plan-topbar-title" title={data?.title}>
            {data ? data.title : "Initiative"}
          </span>
        }
      />

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
              <div className="plan-heading-row">
                <span className="plan-mono plan-muted">{data.external_id}</span>
                <Button
                  variant="primary"
                  compact
                  disabled={!data.milestones.length || planner.isBusy}
                  title={
                    data.milestones.length
                      ? "Ask the planner to propose a schedule"
                      : "Attach a milestone to this initiative first"
                  }
                  onClick={draftSchedule}
                >
                  {planner.isBusy ? "Planning…" : "Draft schedule"}
                </Button>
              </div>
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

            <InitiativeAutopilot
              plan={data}
              busy={busy}
              onAutopilot={(change) => autopilot.mutate(change)}
              onNeedsPerson={(ids, value) => needsPerson.mutate({ ids, value })}
            />

            <ScheduleSummary
              plan={data}
              busy={busy}
              onModeChange={(mode) => edit.mutate({ mode })}
              onTargetChange={(date) => edit.mutateAsync({ targets: [{ ticket_id: data.id, target_date: date }] })}
            />

            <div className="tab-bar">
              <div className="tab-bar-scroll" role="tablist" aria-label="Plan views">
                {TABS.map((key) => (
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
                    {TAB_LABEL[key]}
                    {key === "schedule" && data.pending_proposal ? (
                      <span className="plan-tab-flag">proposal waiting</span>
                    ) : null}
                  </Button>
                ))}
              </div>
            </div>
            <div role="tabpanel" id={`plan-panel-${tab}`} aria-labelledby={`plan-tab-${tab}`}>
              {tab === "schedule" ? (
                <div className="plan-schedule-panel">
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

                  <ScheduleTable
                    plan={data}
                    busy={busy}
                    homes={homes}
                    untracking={untrack.isPending ? (untrack.variables ?? null) : null}
                    onReorder={reorder}
                    onTarget={setTarget}
                    onUntrack={(row) => untrack.mutate(row.id)}
                  />

                  <TrackTicketSearch initiativeId={data.id} onChanged={membershipChanged} />

                  {data.notes ? (
                    <details className="plan-notes">
                      <summary>Why the dates are what they are</summary>
                      <p>{data.notes}</p>
                    </details>
                  ) : null}
                </div>
              ) : tab === "gantt" ? (
                <InitiativeGantt plan={data} />
              ) : (
                <InitiativeBoard
                  initiativeId={data.id}
                  milestones={data.milestones}
                  nodes={data.nodes}
                  onNeedsPerson={(ids, value) => needsPerson.mutateAsync({ ids, value })}
                  onStart={(ids) => startWork.mutateAsync(ids)}
                />
              )}
            </div>
          </main>
          <RedraftConfirmModal
            replaces={confirmingDraft ? scheduleToReplace(data) : null}
            onClose={() => setConfirmingDraft(false)}
            onConfirm={() => {
              setConfirmingDraft(false);
              planner.draft();
            }}
          />
        </>
      ) : null}
    </div>
  );
}
