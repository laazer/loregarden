import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, type Approval, type StageStatus, type TicketDetail, type TicketTreeNode, type WorkItemType } from "../api/client";
import { collectExpandableIds, findAncestorIds, TicketTree } from "../components/TicketTree";
import { STATE_COLORS, STATE_LABELS, UpdateStateModal, type StateUpdateDraft } from "../components/UpdateStateModal";
import { useUiStore } from "../state/uiStore";

function flattenTree(nodes: TicketTreeNode[]): TicketTreeNode[] {
  const out: TicketTreeNode[] = [];
  for (const n of nodes) {
    out.push(n);
    out.push(...flattenTree(n.children));
  }
  return out;
}

const TYPE_FILTERS: { id: WorkItemType | "all"; label: string }[] = [
  { id: "all", label: "All types" },
  { id: "milestone", label: "Milestones" },
  { id: "feature", label: "Features" },
  { id: "capability", label: "Capabilities" },
  { id: "task", label: "Tasks" },
  { id: "bug", label: "Bugs" },
];

const PRIO_BARS: Record<number, string[]> = {
  1: ["var(--red)", "var(--red)", "var(--red)"],
  2: ["var(--amb)", "var(--amb)", "var(--bd2)"],
  3: ["var(--txm)", "var(--bd2)", "var(--bd2)"],
};

function PrioBars({ priority }: { priority: number }) {
  const bars = PRIO_BARS[priority] ?? PRIO_BARS[3];
  return (
    <div className="prio-bars">
      {bars.map((c, i) => (
        <span key={i} style={{ height: 6 + i * 3, background: c }} />
      ))}
    </div>
  );
}

export function Dashboard() {
  const qc = useQueryClient();
  const {
    selectedTicketId,
    filter,
    typeFilter,
    cycleFilter,
    search,
    expandedTicketIds,
    workspace,
    tab,
    inboxOpen,
    setSelectedTicketId,
    setFilter,
    setTypeFilter,
    setCycleFilter,
    setSearch,
    toggleExpanded,
    expandAll,
    collapseAll,
    expandPath,
    setWorkspace,
    setTab,
    setInboxOpen,
  } = useUiStore();

  const wsParam = workspace === "all" ? undefined : workspace;

  const cycles = useQuery({
    queryKey: ["cycles", workspace],
    queryFn: () => api.cycles(wsParam),
  });

  const ticketTree = useQuery({
    queryKey: ["ticket-tree", workspace, filter, typeFilter, cycleFilter, search],
    queryFn: () =>
      api.ticketTree({
        workspace: wsParam,
        state: filter === "all" ? undefined : filter,
        work_item_type: typeFilter === "all" ? undefined : typeFilter,
        cycle_id: cycleFilter === "all" ? undefined : cycleFilter,
        search: search.trim() || undefined,
      }),
    refetchInterval: 5000,
  });

  const flatTickets = useMemo(
    () => flattenTree(ticketTree.data ?? []),
    [ticketTree.data],
  );

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const workflowTemplates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: api.workflowTemplates,
  });

  const selectedId =
    selectedTicketId ??
    flatTickets.find((t) => t.work_item_type === "task" || t.work_item_type === "bug")?.id ??
    flatTickets[0]?.id ??
    null;

  useEffect(() => {
    if (!selectedId || !ticketTree.data?.length) return;
    const ancestors = findAncestorIds(ticketTree.data, selectedId);
    if (ancestors.length) expandPath(ancestors);
  }, [selectedId, ticketTree.data, expandPath]);

  const detail = useQuery({
    queryKey: ["ticket", selectedId],
    queryFn: () => api.ticket(selectedId!),
    enabled: !!selectedId,
    refetchInterval: 3000,
  });

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: api.approvals,
    refetchInterval: 5000,
  });

  const startRun = useMutation({
    mutationFn: () => api.startRun(selectedId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ticket", selectedId] }),
  });

  const advance = useMutation({
    mutationFn: () => api.advance(selectedId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    },
  });

  const resolveApproval = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      api.resolveApproval(id, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  const [stateModalOpen, setStateModalOpen] = useState(false);

  const saveStateFromModal = useMutation({
    mutationFn: async ({
      draft,
      original,
    }: {
      draft: StateUpdateDraft;
      original: StateUpdateDraft;
    }) => {
      if (!selectedId) return;

      const patch: Parameters<typeof api.updateTicket>[1] = {};
      if (draft.state !== original.state) {
        patch.state = draft.state;
        patch.auto_state = false;
      } else if (draft.stateLocked !== original.stateLocked) {
        patch.auto_state = !draft.stateLocked;
      }
      if (draft.workflowStageKey !== original.workflowStageKey) {
        patch.workflow_stage_key = draft.workflowStageKey;
      }
      if (draft.workflowStageStatus !== original.workflowStageStatus) {
        patch.workflow_stage_status = draft.workflowStageStatus;
      }

      const stageUpdates: Record<string, StageStatus> = {};
      for (const [key, status] of Object.entries(draft.stageStatuses)) {
        if (original.stageStatuses[key] !== status) {
          stageUpdates[key] = status;
        }
      }
      if (Object.keys(stageUpdates).length > 0) {
        patch.stage_updates = stageUpdates;
      }

      if (Object.keys(patch).length > 0) {
        await api.updateTicket(selectedId, patch);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      setStateModalOpen(false);
    },
  });

  const setTemplate = useMutation({
    mutationFn: ({ slug, template }: { slug: string; template: string }) =>
      api.setWorkspaceTemplate(slug, template),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-workflow"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  const sel = detail.data;

  const activeWorkspaceSlug =
    workspace === "all" ? (sel?.workspace_slug ?? "loregarden") : workspace;
  const workspaceWorkflow = useQuery({
    queryKey: ["workspace-workflow", activeWorkspaceSlug],
    queryFn: () => api.workspaceWorkflow(activeWorkspaceSlug),
    enabled: !!activeWorkspaceSlug && activeWorkspaceSlug !== "all",
  });
  const ticketRuns = useQuery({
    queryKey: ["runs", selectedId],
    queryFn: () => api.runs(selectedId!),
    enabled: !!selectedId,
  });

  const counts = flatTickets.reduce(
    (acc, t) => {
      if (t.work_item_type === "task" || t.work_item_type === "bug") {
        acc.all += 1;
        acc[t.state] += 1;
      }
      return acc;
    },
    { all: 0, backlog: 0, in_progress: 0, blocked: 0, done: 0, wont_do: 0 } as Record<string, number>,
  );

  const expandedSet = useMemo(() => new Set(expandedTicketIds), [expandedTicketIds]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <div className="brand-mark-inner" />
          </div>
          <div>
            <div className="brand-title">loregarden</div>
            <div className="brand-sub">Agent SDLC</div>
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button
          className="btn-secondary"
          onClick={() => setInboxOpen(true)}
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          Approvals
          <span
            style={{
              minWidth: 19,
              height: 19,
              padding: "0 5px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "var(--red)",
              color: "#fff",
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 10,
              fontFamily: "var(--mono)",
            }}
          >
            {approvals.data?.length ?? 0}
          </span>
        </button>
      </header>

      <div className="main-panes">
        <aside className="sidebar">
          <div className="workspaces-pane">
            <div className="pane-header">
              <span className="pane-title">Workspaces</span>
              <span className="count-pill">{(workspaces.data?.length ?? 0) + 1}</span>
            </div>
            <div className="scroll-list">
              <button
                className={`list-btn ${workspace === "all" ? "active" : ""}`}
                onClick={() => setWorkspace("all")}
                style={{ display: "flex", alignItems: "center", gap: 11 }}
              >
                <span style={{ fontWeight: 500, flex: 1 }}>All workspaces</span>
                <span className="count-pill">{flatTickets.length}</span>
              </button>
              {workspaces.data?.map((w) => (
                <button
                  key={w.id}
                  className={`list-btn ${workspace === w.slug ? "active" : ""}`}
                  onClick={() => setWorkspace(w.slug)}
                  style={{ display: "flex", flexDirection: "column", alignItems: "stretch", gap: 4 }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                    <span style={{ fontWeight: 500, flex: 1 }}>{w.name}</span>
                    {w.blocked_count > 0 && (
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--red)" }} />
                    )}
                    <span className="count-pill">{w.ticket_count}</span>
                  </div>
                  {w.workflow_template_slug && (
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--txl)", paddingLeft: 2 }}>
                      {w.workflow_template_slug}
                      {!w.repo_exists && " · repo missing"}
                    </span>
                  )}
                </button>
              ))}
              {workspace !== "all" && workflowTemplates.data && (
                <div style={{ padding: "8px 4px 0" }}>
                  <div className="state-label" style={{ marginBottom: 6 }}>
                    Workflow template
                  </div>
                  <select
                    className="btn-secondary"
                    style={{ width: "100%", fontSize: 12 }}
                    value={
                      workspaces.data?.find((w) => w.slug === workspace)?.workflow_template_slug ?? ""
                    }
                    onChange={(e) =>
                      setTemplate.mutate({ slug: workspace, template: e.target.value })
                    }
                  >
                    {workflowTemplates.data.map((t) => (
                      <option key={t.slug} value={t.slug}>
                        {t.name} ({t.stage_count} stages)
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>

          <div className="tickets-pane">
            <div className="pane-header tickets-pane-header">
              <div className="tickets-pane-toolbar">
                <span className="pane-title">Work items</span>
                <span className="count-pill">{flatTickets.length}</span>
                <div className="tree-toolbar-actions">
                  <button
                    className="btn-secondary btn-compact"
                    type="button"
                    title="Expand all branches"
                    onClick={() => expandAll(collectExpandableIds(ticketTree.data ?? []))}
                  >
                    Expand all
                  </button>
                  <button
                    className="btn-secondary btn-compact"
                    type="button"
                    title="Collapse all branches"
                    onClick={() => collapseAll()}
                  >
                    Collapse all
                  </button>
                </div>
              </div>
              <input
                className="ticket-search"
                placeholder="Search title or id…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <div className="filter-row">
                <select
                  className="filter-select"
                  value={typeFilter}
                  onChange={(e) => setTypeFilter(e.target.value as WorkItemType | "all")}
                >
                  {TYPE_FILTERS.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.label}
                    </option>
                  ))}
                </select>
                <select
                  className="filter-select"
                  value={cycleFilter}
                  onChange={(e) => setCycleFilter(e.target.value)}
                >
                  <option value="all">All cycles</option>
                  {(cycles.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.ticket_count})
                    </option>
                  ))}
                </select>
              </div>
              <div className="state-filters">
                {(["all", "backlog", "in_progress", "blocked", "done", "wont_do"] as const).map((f) => (
                  <button
                    key={f}
                    className="btn-secondary btn-compact"
                    style={{
                      borderColor: filter === f ? "var(--ac)" : undefined,
                      color: filter === f ? "var(--ac2)" : undefined,
                    }}
                    onClick={() => setFilter(f)}
                  >
                    {f === "all" ? "All" : STATE_LABELS[f]}{" "}
                    <span className="filter-count">{counts[f]}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="scroll-list">
              {ticketTree.data?.length ? (
                <TicketTree
                  nodes={ticketTree.data}
                  selectedId={selectedId}
                  expandedIds={expandedSet}
                  onSelect={setSelectedTicketId}
                  onToggle={toggleExpanded}
                />
              ) : (
                <div className="empty-tree">No work items match filters</div>
              )}
            </div>
          </div>
        </aside>

        <main className="workflow-pane">
          {sel ? (
            <>
              <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
                <div style={{ display: "flex", gap: 12, marginBottom: 14 }}>
                  <PrioBars priority={sel.priority} />
                  <h1 style={{ margin: 0, fontFamily: "var(--dp)", fontSize: 19, fontWeight: 600 }}>
                    {sel.title}
                  </h1>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                  <span className="count-pill">{sel.workspace_slug}</span>
                  <span className="count-pill">{sel.work_item_type}</span>
                  {sel.cycle_name && <span className="count-pill">{sel.cycle_name}</span>}
                  <span className="count-pill">{sel.branch || "—"}</span>
                  {workspaceWorkflow.data?.template_slug && (
                    <span className="count-pill" style={{ color: "var(--ac2)" }}>
                      {workspaceWorkflow.data.template_name}
                    </span>
                  )}
                </div>
                <div className="dual-state">
                  <div className="state-card">
                    <div className="state-label">Ticket state · WHAT</div>
                    <div style={{ fontWeight: 600, color: STATE_COLORS[sel.state] }}>
                      {STATE_LABELS[sel.state]}
                    </div>
                    {sel.state_locked && (
                      <span className="count-pill" style={{ marginTop: 8, fontSize: 10 }}>
                        locked
                      </span>
                    )}
                  </div>
                  <div className="state-card">
                    <div className="state-label">Workflow · HOW</div>
                    <div style={{ fontWeight: 600 }}>
                      {sel.workflow_stage_name || sel.workflow_stage_key || "—"}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 4 }}>
                      {sel.workflow_stage_status.replace("_", " ")}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ marginTop: 10 }}
                  onClick={() => setStateModalOpen(true)}
                >
                  Update state…
                </button>

                {sel.blocking_issues && (
                  <div
                    style={{
                      marginTop: 16,
                      padding: 12,
                      borderRadius: 11,
                      background: "rgba(240,96,63,.1)",
                      border: "1px solid rgba(240,96,63,.3)",
                      fontSize: 12,
                      color: "var(--rdl)",
                    }}
                  >
                    {sel.blocking_issues}
                  </div>
                )}

                <div style={{ marginTop: 24 }}>
                  <div className="state-label" style={{ marginBottom: 16 }}>
                    Workflow lifecycle
                  </div>
                  {sel.stages.map((s) => (
                    <div key={s.key} className="stage-row" style={{ paddingBottom: 18 }}>
                      <div
                        className={`stage-dot ${s.status}`}
                        style={{ marginTop: 3 }}
                      />
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span style={{ fontWeight: s.status === "running" ? 600 : 400 }}>{s.name}</span>
                          {s.optional && (
                            <span className="count-pill" style={{ fontSize: 9 }}>
                              optional
                            </span>
                          )}
                          <span
                            className="count-pill"
                            style={{
                              marginLeft: "auto",
                              color:
                                s.status === "running"
                                  ? "var(--bll)"
                                  : s.status === "done"
                                    ? "var(--grl)"
                                    : "var(--txl)",
                            }}
                          >
                            {s.status}
                          </span>
                        </div>
                        {s.agent_id && (
                          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)", marginTop: 5 }}>
                            {s.agent_id}
                            {s.skill_name ? ` · ${s.skill_name}` : ""}
                          </div>
                        )}
                        {s.note && (
                          <div
                            style={{
                              marginTop: 7,
                              fontSize: 11.5,
                              padding: "8px 10px",
                              background: "var(--bg2)",
                              border: "1px solid var(--bd)",
                              borderRadius: 8,
                            }}
                          >
                            {s.note}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="run-controls">
                <button
                  className="btn-primary"
                  disabled={!selectedId || startRun.isPending}
                  onClick={() => startRun.mutate()}
                >
                  Start run
                </button>
                <button
                  className="btn-secondary"
                  disabled={!selectedId || advance.isPending}
                  onClick={() => advance.mutate()}
                >
                  Advance stage
                </button>
                <div style={{ flex: 1 }} />
                <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
                  {sel.run_code || "—"}
                </span>
              </div>
            </>
          ) : (
            <div style={{ padding: 40, color: "var(--txl)" }}>Select a ticket</div>
          )}
        </main>

        <section className="artifacts-pane">
          <div className="tab-bar">
            {(["diff", "logs", "tests", "context"] as const).map((t) => (
              <button
                key={t}
                className={`tab-btn ${tab === t ? "active" : ""}`}
                onClick={() => setTab(t)}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
            <div style={{ flex: 1 }} />
            <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
              truth layer · execution output only
            </span>
          </div>
          <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
            <ArtifactView tab={tab} ticket={sel} runs={ticketRuns.data ?? []} />
          </div>
        </section>
      </div>

      <UpdateStateModal
        open={stateModalOpen}
        ticket={sel ?? null}
        workflowStages={workspaceWorkflow.data?.stages ?? []}
        isSaving={saveStateFromModal.isPending}
        onClose={() => setStateModalOpen(false)}
        onSave={(draft, original) => saveStateFromModal.mutateAsync({ draft, original })}
      />

      {inboxOpen && (
        <>
          <div className="inbox-overlay" onClick={() => setInboxOpen(false)} />
          <aside className="inbox-panel">
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--bd)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="pane-title">Global Approval Inbox</span>
                <span className="count-pill">{approvals.data?.length ?? 0}</span>
                <div style={{ flex: 1 }} />
                <button className="btn-secondary" onClick={() => setInboxOpen(false)}>
                  ✕
                </button>
              </div>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
              {approvals.data?.map((a) => (
                <ApprovalCard
                  key={a.id}
                  approval={a}
                  onApprove={() => resolveApproval.mutate({ id: a.id, action: "approve" })}
                  onReject={() => resolveApproval.mutate({ id: a.id, action: "reject" })}
                  onInspect={() => {
                    setSelectedTicketId(a.ticket_id);
                    setInboxOpen(false);
                    setTab("diff");
                  }}
                />
              ))}
              {!approvals.data?.length && (
                <div style={{ textAlign: "center", color: "var(--txm)", padding: 40 }}>
                  Inbox zero — nothing needs your attention
                </div>
              )}
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

function ApprovalCard({
  approval,
  onApprove,
  onReject,
  onInspect,
}: {
  approval: Approval;
  onApprove: () => void;
  onReject: () => void;
  onInspect: () => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--bd)",
        borderRadius: 12,
        background: "var(--bg2)",
        marginBottom: 10,
        overflow: "hidden",
      }}
    >
      <div style={{ padding: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>{approval.title}</div>
        <div style={{ fontSize: 11, color: "var(--txl)", marginBottom: 8 }}>
          {approval.workspace_slug} · {approval.stage_name}
        </div>
        <p style={{ margin: 0, fontSize: 12, color: "var(--txm)", lineHeight: 1.55 }}>{approval.impact}</p>
      </div>
      <div style={{ display: "flex", borderTop: "1px solid var(--bd)" }}>
        <button className="btn-secondary" style={{ flex: 1, borderRadius: 0, color: "var(--grl)" }} onClick={onApprove}>
          Approve
        </button>
        <button className="btn-secondary" style={{ flex: 1, borderRadius: 0, color: "var(--rdl)" }} onClick={onReject}>
          Reject
        </button>
        <button className="btn-secondary" style={{ flex: 1, borderRadius: 0 }} onClick={onInspect}>
          Inspect
        </button>
      </div>
    </div>
  );
}

function ArtifactView({
  tab,
  ticket,
  runs = [],
}: {
  tab: string;
  ticket?: TicketDetail;
  runs?: { id: string; run_code: string; status: string; command: string }[];
}) {
  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }
  const art = ticket.artifacts ?? {};

  if (tab === "diff") {
    const diff = art.diff;
    if (!diff) {
      return <EmptyArtifacts />;
    }
    return (
      <div>
        <div style={{ padding: "11px 18px", borderBottom: "1px solid var(--bd)", background: "var(--bg1)" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{diff.file}</span>
          <span style={{ marginLeft: 12, color: "var(--grl)", fontFamily: "var(--mono)" }}>{diff.add}</span>
          <span style={{ marginLeft: 8, color: "var(--rdl)", fontFamily: "var(--mono)" }}>{diff.del}</span>
        </div>
        <div style={{ padding: "6px 0" }}>
          {diff.lines?.map((line, i) => (
            <div
              key={i}
              className={`diff-line ${line.type === "a" ? "add" : line.type === "d" ? "del" : ""}`}
            >
              <span style={{ width: 44, textAlign: "right", paddingRight: 12, color: "var(--txl)" }}>
                {line.ln}
              </span>
              <span style={{ width: 15, textAlign: "center" }}>
                {line.type === "a" ? "+" : line.type === "d" ? "−" : " "}
              </span>
              <span style={{ flex: 1, whiteSpace: "pre" }}>{line.text}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (tab === "logs") {
    const lines = art.logs ?? [];
    if (!lines.length && !art.live) return <EmptyArtifacts />;
    return (
      <div style={{ fontFamily: "var(--mono)", fontSize: 12, padding: 16, lineHeight: 1.75 }}>
        {lines.map((l, i) => (
          <div key={i} style={{ display: "flex", gap: 12 }}>
            <span style={{ color: "var(--txl)" }}>{l.time}</span>
            <span style={{ width: 44, textAlign: "center", fontSize: 10, fontWeight: 600 }}>{l.tag}</span>
            <span style={{ color: "var(--txm)" }}>{l.text}</span>
          </div>
        ))}
        {art.live && <div style={{ color: "var(--bll)", marginTop: 8 }}>{art.live} ▊</div>}
      </div>
    );
  }

  if (tab === "tests") {
    const tests = art.tests;
    if (!tests) return <EmptyArtifacts />;
    return (
      <div style={{ padding: 16 }}>
        <div className="state-card" style={{ marginBottom: 14 }}>
          <strong>{tests.summary}</strong>
          <span style={{ float: "right", fontFamily: "var(--mono)", fontSize: 11, color: "var(--txl)" }}>
            {tests.cmd}
          </span>
        </div>
        {tests.rows?.map((row, i) => (
          <div key={i} className="list-btn" style={{ marginBottom: 4 }}>
            <span style={{ color: row.status === "pass" ? "var(--grl)" : "var(--rdl)" }}>{row.status}</span>{" "}
            <span style={{ fontFamily: "var(--mono)" }}>{row.name}</span>
            {row.msg && <div style={{ color: "var(--rdl)", fontSize: 11, marginTop: 4 }}>{row.msg}</div>}
          </div>
        ))}
      </div>
    );
  }

  const sections = art.context ?? [];
  const runRows = runs;
  if (!sections.length && !runRows.length) return <EmptyArtifacts />;
  return (
    <div style={{ padding: 16 }}>
      {runRows.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div className="state-label">Agent runs</div>
          {runRows.map((r) => (
            <div key={r.id} className="list-btn" style={{ marginBottom: 4, fontFamily: "var(--mono)", fontSize: 11 }}>
              {r.run_code} · {r.status} · {r.command.slice(0, 60)}
            </div>
          ))}
        </div>
      )}
      {sections.map((sec, i) => (
        <div key={i} style={{ marginBottom: 16 }}>
          <div className="state-label">{sec.title}</div>
          {sec.rows?.map((r, j) => (
            <div key={j} style={{ display: "flex", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--bd)" }}>
              <span style={{ width: 130, color: "var(--txm)" }}>{r.k}</span>
              <span style={{ fontFamily: "var(--mono)", flex: 1 }}>{r.v}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function EmptyArtifacts() {
  return (
    <div style={{ height: "100%", minHeight: 340, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--txl)", gap: 12 }}>
      <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>No artifacts yet</div>
      <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 280 }}>Artifacts appear when agent runs complete</div>
    </div>
  );
}
