import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, type WorkItemType } from "../api/client";
import { CopyValueButton } from "../components/CopyValueButton";
import { ArtifactPaneBody } from "../components/dashboard/ArtifactPaneBody";
import { ArtifactTabBar } from "../components/dashboard/ArtifactTabBar";
import { CreateWorkItemFlow, type CreateWorkItemRequest } from "../components/dashboard/CreateWorkItemFlow";
import { WorkflowRunControls } from "../components/dashboard/WorkflowRunControls";
import { DashboardTicketDetailsButton } from "../components/DashboardTicketDetailsButton";
import { PrioBars } from "../components/PrioBars";
import { TicketGithubSyncButton } from "../components/TicketGithubSyncButton";
import { TicketFailurePanel } from "../components/ticket/TicketFailurePanel";
import { TicketWorkflowColumn } from "../components/ticket/TicketWorkflowColumn";
import { TicketWorkModals } from "../components/ticket/TicketWorkModals";
import { Button } from "../components/ui/Button";
import { WorkflowPaneTicketMeta } from "../components/WorkflowPaneTicketMeta";
import { useTicketWork } from "../hooks/useTicketWork";
import { type ArtifactTab, canonicalArtifactTab, isArtifactTab, ticketPath } from "../lib/appNavigation";
import {
  DEFAULT_ARTIFACT_TAB,
  navigateToTicket,
  navigateToTicketTab,
  useArtifactTabFromRoute,
  useTicketIdFromRoute,
} from "../lib/useAppNavigation";
import { canHaveChildren } from "../lib/workItemHierarchy";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { useUiStore } from "../state/uiStore";
import { describeError } from "../state/toastStore";

import "./TicketPage.css";

/** The page's three views. Ticket holds the reading tabs; Diff and Hive each fill the page. */
type TicketView = "ticket" | "diff" | "hive";

const VIEW_LABELS: Record<TicketView, string> = { ticket: "Ticket", diff: "Diff", hive: "Hive" };
const VIEWS: readonly TicketView[] = ["ticket", "diff", "hive"];
const READING_TABS: readonly ArtifactTab[] = ["timeline", "outputs", "pr", "approvals"];
const MORE_READING_TABS: readonly ArtifactTab[] = ["monitor"];

function viewForTab(tab: ArtifactTab): TicketView {
  if (tab === "diff") return "diff";
  if (tab === "hive") return "hive";
  return "ticket";
}

/** Below this the workflow, the outputs and Baxter cannot sit side by side; he docks at the bottom. */
const GUIDE_NARROW = "(max-width: 1280px)";

type SubTicketParent = { id: string; title: string; work_item_type: WorkItemType; workspace_slug?: string };

/**
 * One ticket, full width: where it sits and what it is at the top, its
 * workflow down the left, and what its runs produced in the main column. The
 * Console is where tickets are browsed; this is where one is worked.
 */
export function TicketPage() {
  const ticketId = useTicketIdFromRoute();
  const { artifactTab: rawArtifactTab } = useParams<{ artifactTab?: string }>();
  const artifactTab = useArtifactTabFromRoute();
  const view = viewForTab(artifactTab);
  const work = useTicketWork(ticketId);
  const { sel } = work;
  const { expandedTicketIds, toggleExpanded, openEditorFile } = useUiStore();
  const expandedSet = useMemo(() => new Set(expandedTicketIds), [expandedTicketIds]);
  const [createRequest, setCreateRequest] = useState<CreateWorkItemRequest | null>(null);
  // The reading tab to return to from Diff or Hive.
  const [lastReadingTab, setLastReadingTab] = useState<ArtifactTab>(DEFAULT_ARTIFACT_TAB);

  useEffect(() => {
    if (view === "ticket") setLastReadingTab(artifactTab);
  }, [view, artifactTab]);

  // Baxter is the page's guide: on a wide window his conversation opens docked
  // on the right, the composer inside it, as on the initiative plan page.
  // Opened per ticket, so arriving at one shows its chat; the edge is the
  // screen's, so leaving gives the operator's own back.
  const narrow = useMediaQuery(GUIDE_NARROW);
  const setCopilotOpen = useUiStore((s) => s.setCopilotOpen);
  const setScreenDockEdge = useUiStore((s) => s.setScreenDockEdge);
  useEffect(() => {
    if (!narrow && ticketId) setCopilotOpen(true);
  }, [narrow, ticketId, setCopilotOpen]);
  useEffect(() => {
    if (narrow) return undefined;
    setScreenDockEdge("right");
    return () => setScreenDockEdge(null);
  }, [narrow, setScreenDockEdge]);

  useEffect(() => {
    if (!ticketId || !rawArtifactTab || isArtifactTab(rawArtifactTab)) return;
    // A retired tab (/logs, /errors, /artifacts…) lands on the view that holds it now.
    navigateToTicket(ticketId, { tab: canonicalArtifactTab(rawArtifactTab) ?? DEFAULT_ARTIFACT_TAB, replace: true });
  }, [ticketId, rawArtifactTab]);

  const parentId = sel?.parent_ticket_id ?? null;
  const parent = useQuery({
    queryKey: ["ticket", parentId],
    queryFn: () => api.ticket(parentId!),
    enabled: Boolean(parentId),
  });

  const openTicket = (id: string) => navigateToTicket(id, { tab: lastReadingTab });

  const openCreateSubTicket = (target: SubTicketParent) => {
    if (!canHaveChildren(target.work_item_type)) return;
    const slug = target.workspace_slug || work.activeWorkspaceSlug;
    if (!slug) return;
    setCreateRequest({ workspaceSlug: slug, parent: { id: target.id, title: target.title, type: target.work_item_type } });
  };

  const goToView = (next: TicketView) => {
    if (!ticketId) return;
    navigateToTicketTab(ticketId, next === "ticket" ? lastReadingTab : next);
  };

  if (!sel) {
    return (
      <div className="screen-view ticket-page">
        <TicketPageFallback loading={work.detail.isPending} error={work.detail.error} onRetry={() => void work.detail.refetch()} />
      </div>
    );
  }

  return (
    <div className="screen-view ticket-page">
      <header className="ticket-page-header">
        <nav className="ticket-page-crumbs" aria-label="Breadcrumb">
          <ol>
            <li>
              <Link to="/console">Console</Link>
            </li>
            {parentId ? (
              <li>
                <Link to={ticketPath(parentId, lastReadingTab)} className="ticket-page-crumb-parent">
                  {parent.data?.title ?? parent.data?.external_id ?? "Parent"}
                </Link>
              </li>
            ) : null}
            <li aria-current="page">
              <CopyValueButton value={sel.external_id} what={`ticket id ${sel.external_id}`} className="ticket-page-id">
                {sel.external_id}
              </CopyValueButton>
            </li>
          </ol>
        </nav>

        <div className="ticket-page-views" role="tablist" aria-label="Ticket page views">
          {VIEWS.map((v) => (
            <Button
              key={v}
              variant="plain"
              role="tab"
              aria-selected={view === v}
              className={`ticket-page-view${view === v ? " is-active" : ""}`}
              onClick={() => goToView(v)}
            >
              {VIEW_LABELS[v]}
            </Button>
          ))}
        </div>

        <div className="ticket-page-title-row">
          <PrioBars priority={sel.priority} size="md" />
          <h1 className="ticket-page-title">{sel.title}</h1>
          <div className="ticket-page-actions">
            <TicketGithubSyncButton ticketId={sel.id} />
            <DashboardTicketDetailsButton ticketId={sel.id} />
            <WorkflowRunControls
              placement="header"
              ticket={sel}
              workflowTemplates={work.workflowTemplates.data}
              hasActiveRun={work.hasActiveRun}
              workflowBusy={work.workflowBusy}
              startRunPending={work.startRun.isPending}
              assemblePending={work.orchestrate.isPending && work.orchestrate.variables?.ticketId === sel.id}
              pausePending={work.stopTicket.isPending}
              isStageRunning={work.isStageRunning}
              onAssemble={() => work.setAssembleModalOpen(true)}
              onPause={() => work.stopTicket.mutate()}
              onRerun={work.setRunConfirmStageKey}
              onDelete={() => work.setDeleteTicketTarget(sel)}
              onEditState={() => work.setStateModalOpen(true)}
            />
          </div>
        </div>
        <WorkflowPaneTicketMeta
          ticket={sel}
          hasRunErrors={work.hasRunErrors}
          failureShown={view === "ticket"}
          onAddChild={openCreateSubTicket}
        />
      </header>

      <div className="ticket-page-body">
        {view === "ticket" ? (
          <aside className="ticket-page-workflow" aria-label="Workflow">
            <TicketWorkflowColumn
              ticket={sel}
              work={work}
              expandedIds={expandedSet}
              onOpenTicket={openTicket}
              onToggleChild={toggleExpanded}
              onAddChild={openCreateSubTicket}
            />
          </aside>
        ) : null}
        <section className="ticket-page-main" aria-label={VIEW_LABELS[view]}>
          {view === "ticket" ? (
            <TicketFailurePanel
              ticket={sel}
              runs={work.ticketRuns.data ?? []}
              isActive={work.hasActiveRun || sel.workflow_stage_status === "running"}
              onOpenRunLog={work.setLogRunId}
            />
          ) : null}
          {view === "ticket" ? (
            <div className="tab-bar">
              <ArtifactTabBar
                artifactTab={artifactTab}
                selectedId={sel.id}
                hasRunErrors={work.hasRunErrors}
                outputCount={work.artifactsFeed.data?.items.filter((item) => !item.system).length ?? 0}
                approvalCount={work.pendingApprovalCount}
                hasPr={!!sel.artifacts?.pr}
                primaryTabs={READING_TABS}
                moreTabs={MORE_READING_TABS}
              />
            </div>
          ) : null}
          <div className="artifact-tab-body">
            <ArtifactPaneBody
              artifactTab={artifactTab}
              ticket={sel}
              hasActiveRun={work.hasActiveRun}
              pendingApprovals={work.pendingApprovalCount}
              childTickets={work.children}
              selectedId={sel.id}
              activeWorkspaceSlug={work.activeWorkspaceSlug}
              isOpeningPr={work.openPr.isPending}
              isCommittingPush={work.commitPush.isPending}
              onOpenRunLog={work.setLogRunId}
              onOpenTicket={openTicket}
              onOpenEditorFile={openEditorFile}
              onOpenPr={() => work.openPr.mutate(sel.id)}
              onCommitPush={() => work.commitPush.mutate(sel.id)}
            />
          </div>
        </section>
      </div>

      <TicketWorkModals work={work} />

      {createRequest && (
        <CreateWorkItemFlow
          request={createRequest}
          workspaceIsAll={false}
          workspaces={work.activeWorkspaces}
          selectedTicketId={sel.id}
          ticketTree={work.workspaceTree}
          onCreated={(ticket) => navigateToTicket(ticket.id, { replace: true })}
          onClose={() => setCreateRequest(null)}
        />
      )}
    </div>
  );
}

/** What stands in for the page before the ticket arrives, or when it cannot. */
function TicketPageFallback({
  loading,
  error,
  onRetry,
}: {
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <div className="ticket-page-fallback" role="alert">
        <p>This ticket could not be loaded: {describeError(error)}. Try again, or go back and pick it from the Console.</p>
        <div className="ticket-page-fallback-actions">
          <Button variant="primary" onClick={onRetry}>
            Try again
          </Button>
          <Link to="/console">Back to the Console</Link>
        </div>
      </div>
    );
  }
  if (loading) {
    return (
      <div className="ticket-page-skeleton" aria-busy="true" aria-label="Loading ticket">
        <div className="ticket-page-skeleton-line ticket-page-skeleton-line--short" />
        <div className="ticket-page-skeleton-line ticket-page-skeleton-line--title" />
        <div className="ticket-page-skeleton-line" />
      </div>
    );
  }
  return (
    <div className="ticket-page-fallback">
      <p>No ticket here. Pick one from the Console.</p>
      <Link to="/console">Open the Console</Link>
    </div>
  );
}
