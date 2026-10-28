import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TicketTreeNode, type WorkItemType } from "../api/client";
import { DashboardActiveTickets } from "../components/DashboardActiveTickets";
import { Button } from "../components/ui/Button";
import { TicketPaneFilters } from "../components/TicketPaneFilters";
import { findAncestorIds, TicketTree } from "../components/TicketTree";
import { AddWorkspaceFlow } from "../components/workspaces/AddWorkspaceFlow";
import { CreateWorkItemFlow, type CreateWorkItemRequest } from "../components/dashboard/CreateWorkItemFlow";
import { DashboardWorkspacesPane } from "../components/dashboard/DashboardWorkspacesPane";
import { ImportTicketsFlow } from "../components/dashboard/ImportTicketsFlow";
import { PaneHideButton } from "../components/dashboard/PaneHideButton";
import { TicketPaneViewControls } from "../components/dashboard/TicketPaneViewControls";
import { archivedWorkspaceSlugs, withoutArchivedWorkspaces } from "../lib/archivedWorkspaces";
import { canHaveChildren } from "../lib/workItemHierarchy";
import { navigateToTicket } from "../lib/useAppNavigation";
import { useUiStore, type PaneId } from "../state/uiStore";
import { useExpandSearchMatches } from "../hooks/useExpandSearchMatches";

/** Stable while the tree loads, so memos keyed on it do not recompute every render. */
const NO_NODES: TicketTreeNode[] = [];

/** Every ticket once: an initiative's member copies are skipped, the real rows counted. */
function flattenTree(nodes: TicketTreeNode[]): TicketTreeNode[] {
  const out: TicketTreeNode[] = [];
  for (const n of nodes) {
    if (!n.member_link) out.push(n);
    out.push(...flattenTree(n.children));
  }
  return out;
}

function treeHasRunningWorkflow(nodes: TicketTreeNode[]): boolean {
  for (const n of nodes) {
    if (n.workflow_stage_status === "running") return true;
    if (treeHasRunningWorkflow(n.children)) return true;
  }
  return false;
}

/**
 * The Console: where work items are browsed. Workspaces down the left, the
 * filtered tree across the rest, and what is running now along the top. A
 * ticket opens on its own page.
 */
export function Dashboard() {
  const {
    stateFilters,
    blockedOnFilters,
    typeFilters,
    search,
    setSearch,
    ticketDensity,
    expandedTicketIds,
    workspace,
    toggleStateFilter,
    clearStateFilters,
    toggleBlockedOnFilter,
    clearBlockedOnFilters,
    toggleTypeFilter,
    clearTypeFilters,
    toggleExpanded,
    expandPath,
    setWorkspace,
    paneVisibility,
    setPaneVisible,
  } = useUiStore();

  // The tree is what the Console is for, so it shows whenever the workspaces pane does not.
  const showWorkspaces = paneVisibility.workspaces;
  const showTickets = paneVisibility.tickets || !showWorkspaces;
  const bothPanes = showWorkspaces && showTickets;
  const hidePane = (pane: PaneId) => setPaneVisible(pane, false);

  const wsParam = workspace === "all" ? undefined : workspace;

  const ticketTree = useQuery({
    queryKey: ["ticket-tree", workspace, stateFilters, blockedOnFilters, typeFilters, search],
    queryFn: () =>
      api.ticketTree({
        workspace: wsParam,
        state: stateFilters.length ? stateFilters : undefined,
        blocked_on: blockedOnFilters.length ? blockedOnFilters : undefined,
        work_item_type: typeFilters.length ? typeFilters : undefined,
        search: search.trim() || undefined,
        include_members: true,
      }),
    refetchInterval: (query) =>
      treeHasRunningWorkflow(query.state.data ?? []) ? 1000 : 5000,
  });

  const [createRequest, setCreateRequest] = useState<CreateWorkItemRequest | null>(null);
  const [importSlug, setImportSlug] = useState<string | null>(null);
  const [addWorkspaceOpen, setAddWorkspaceOpen] = useState(false);

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const archivedSlugs = useMemo(() => archivedWorkspaceSlugs(workspaces.data ?? []), [workspaces.data]);
  const activeWorkspaces = useMemo(
    () => (workspaces.data ?? []).filter((w) => !archivedSlugs.has(w.slug)),
    [workspaces.data, archivedSlugs],
  );
  // Archiving a workspace takes it out of the Console: its items leave "All workspaces" too.
  const visibleTree = useMemo(
    () => withoutArchivedWorkspaces(ticketTree.data ?? NO_NODES, archivedSlugs),
    [ticketTree.data, archivedSlugs],
  );
  const flatTickets = useMemo(() => flattenTree(visibleTree), [visibleTree]);
  useExpandSearchMatches(search, ticketTree.data, expandPath);

  useEffect(() => {
    if (workspace !== "all" && archivedSlugs.has(workspace)) setWorkspace("all");
  }, [workspace, archivedSlugs, setWorkspace]);
  const workflowTemplates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: api.workflowTemplates,
  });

  const selectTicket = useCallback((id: string) => navigateToTicket(id), []);

  const defaultCreateWorkspaceSlug = activeWorkspaces[0]?.slug || "loregarden";
  const scopedSlug = workspace === "all" ? defaultCreateWorkspaceSlug : workspace;

  const openCreateSubTicket = (parent: {
    id: string;
    title: string;
    work_item_type: WorkItemType;
    workspace_slug?: string;
  }) => {
    if (!canHaveChildren(parent.work_item_type)) return;
    const slug = parent.workspace_slug || scopedSlug;
    if (!slug) return;
    setCreateRequest({
      workspaceSlug: slug,
      parent: { id: parent.id, title: parent.title, type: parent.work_item_type },
    });
  };

  const counts = flatTickets.reduce(
    (acc, t) => {
      acc.all += 1;
      acc[t.state] += 1;
      return acc;
    },
    { all: 0, backlog: 0, in_progress: 0, blocked: 0, done: 0, wont_do: 0 } as Record<string, number>,
  );
  const blockedOnCounts = flatTickets.reduce<Record<string, number>>((acc, t) => {
    if (t.blocked_on) acc[t.blocked_on] = (acc[t.blocked_on] ?? 0) + 1;
    return acc;
  }, {});

  const expandedSet = useMemo(() => new Set(expandedTicketIds), [expandedTicketIds]);

  return (
    <div className="screen-view">
      {/* Three tickets can hold slots at once now that each runs in its own worktree. */}
      <DashboardActiveTickets onSelect={selectTicket} />
      <div className="main-panes">
        {showWorkspaces && (
          <aside className={`sidebar console-workspaces${showTickets ? "" : " console-workspaces--fill"}`}>
            <DashboardWorkspacesPane
              workspaces={activeWorkspaces}
              archivedCount={archivedSlugs.size}
              selected={workspace}
              allTicketCount={flatTickets.length}
              workflowTemplates={workflowTemplates.data}
              fill
              hideDisabled={!bothPanes}
              onSelect={setWorkspace}
              onAdd={() => setAddWorkspaceOpen(true)}
              onHide={() => hidePane("workspaces")}
            />
          </aside>
        )}

        {showTickets && (
          <main className="tickets-pane console-tickets">
            <PaneHideButton
              className="pane-hide-btn pane-hide-btn--corner"
              pane="tickets"
              onHide={() => hidePane("tickets")}
              disabled={!bothPanes}
            />
            <div className="pane-header tickets-pane-header">
              <div className="tickets-pane-title-row">
                <div className="tickets-pane-heading">
                  <span className="pane-title">Work items</span>
                  <span className="count-pill">{flatTickets.length}</span>
                </div>
                <TicketPaneViewControls />
              </div>
              <div className="tickets-pane-actions">
                <div className="tickets-pane-primary-actions">
                  <Button
                    variant="secondary"
                    compact
                    className="btn-icon-label"
                    title="Create a new work item"
                    onClick={() => setCreateRequest({ workspaceSlug: scopedSlug, parent: null })}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                    New
                  </Button>
                  <Button
                    variant="secondary"
                    compact
                    className="btn-icon-label"
                    title="Import work items from .md, .json, or .yaml files"
                    disabled={importSlug !== null}
                    onClick={() => setImportSlug(scopedSlug)}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                      <path d="M12 3v12" />
                      <path d="m8 11 4 4 4-4" />
                      <path d="M5 21h14" />
                    </svg>
                    Import
                  </Button>
                </div>
              </div>
              <TicketPaneFilters
                search={search}
                onSearch={setSearch}
                typeFilters={typeFilters}
                stateFilters={stateFilters}
                stateCounts={counts}
                blockedOnFilters={blockedOnFilters}
                blockedOnCounts={blockedOnCounts}
                onToggleType={toggleTypeFilter}
                onToggleState={toggleStateFilter}
                onToggleBlockedOn={toggleBlockedOnFilter}
                onClearTypes={clearTypeFilters}
                onClearStates={clearStateFilters}
                onClearBlockedOn={clearBlockedOnFilters}
              />
            </div>
            <div className={ticketDensity === "cards" ? "scroll-list lg-primitive-ticket-list--v6" : "scroll-list"}>
              {ticketTree.isPending ? (
                <div className="empty-tree" aria-busy="true">Loading work items…</div>
              ) : visibleTree.length ? (
                <TicketTree
                  nodes={visibleTree}
                  selectedId={null}
                  expandedIds={expandedSet}
                  onSelect={selectTicket}
                  onToggle={toggleExpanded}
                  onAddChild={openCreateSubTicket}
                  presentation={ticketDensity === "cards" ? "v6" : "compact"}
                />
              ) : search.trim() ? (
                <div className="empty-tree">
                  <p>No work item&apos;s title or id contains “{search.trim()}”.</p>
                  <Button variant="secondary" compact onClick={() => setSearch("")}>
                    Clear the filter
                  </Button>
                </div>
              ) : (
                <div className="empty-tree">No work items match filters</div>
              )}
            </div>
          </main>
        )}
      </div>

      {createRequest && (
        <CreateWorkItemFlow
          request={createRequest}
          workspaceIsAll={workspace === "all"}
          workspaces={activeWorkspaces}
          selectedTicketId={null}
          ticketTree={visibleTree}
          onCreated={(ticket) => {
            if (ticket.parent_ticket_id && ticketTree.data) {
              expandPath([...findAncestorIds(ticketTree.data, ticket.parent_ticket_id), ticket.parent_ticket_id]);
            }
            navigateToTicket(ticket.id);
          }}
          onClose={() => setCreateRequest(null)}
        />
      )}

      {importSlug && (
        <ImportTicketsFlow
          workspaceSlug={importSlug}
          browsePath={workspaces.data?.find((w) => w.slug === importSlug)?.repo_path?.trim() || "."}
          onClose={() => setImportSlug(null)}
        />
      )}

      {addWorkspaceOpen && (
        <AddWorkspaceFlow
          existingSlugs={(workspaces.data ?? []).map((w) => w.slug)}
          onClose={() => setAddWorkspaceOpen(false)}
          onCreated={(created) => {
            setWorkspace(created.slug);
            setAddWorkspaceOpen(false);
          }}
        />
      )}
    </div>
  );
}
