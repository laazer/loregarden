import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, type WorkflowTemplateSummary, type WorkspaceSummary } from "../../api/client";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { workspacesPath } from "../../lib/appNavigation";
import { PaneHideButton } from "./PaneHideButton";

interface DashboardWorkspacesPaneProps {
  /** Active workspaces only; archived ones are counted, not listed. */
  workspaces: WorkspaceSummary[];
  archivedCount: number;
  /** A workspace slug, or "all". */
  selected: string;
  allTicketCount: number;
  workflowTemplates: WorkflowTemplateSummary[] | undefined;
  fill: boolean;
  hideDisabled: boolean;
  onSelect: (slug: string) => void;
  onAdd: () => void;
  onHide: () => void;
}

/**
 * The Console's workspace picker: every active workspace, its ticket count and
 * whether anything in it is blocked, and the selected one's default workflow.
 * Archived workspaces are left out; a link says how many and where they went.
 */
export function DashboardWorkspacesPane({
  workspaces,
  archivedCount,
  selected,
  allTicketCount,
  workflowTemplates,
  fill,
  hideDisabled,
  onSelect,
  onAdd,
  onHide,
}: DashboardWorkspacesPaneProps) {
  const qc = useQueryClient();
  const setTemplate = useMutation({
    meta: { errorTitle: "Set workspace workflow" },
    mutationFn: ({ slug, template }: { slug: string; template: string }) => api.setWorkspaceTemplate(slug, template),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-workflow"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  // Offered only while the selector is: a workspace is selected and the
  // templates have loaded — the same conditions that render the <select>.
  useAgentAction(
    "workspace.set_workflow",
    async ({ workspace_slug, template }) => {
      if (workspace_slug !== selected) {
        throw new Error(`${workspace_slug} is not the selected workspace (${selected}); select it first`);
      }
      if (!workflowTemplates?.some((t) => t.slug === template)) {
        throw new Error(`no workflow template named ${template}`);
      }
      await setTemplate.mutateAsync({ slug: workspace_slug, template });
      return { workspace_slug, template };
    },
    selected !== "all" && Boolean(workflowTemplates),
  );

  return (
    <div className={`workspaces-pane ${fill ? "pane-fill" : ""}`.trim()}>
      <div className="pane-header">
        <span className="pane-title">Workspaces</span>
        <span className="count-pill">{workspaces.length + 1}</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn-secondary btn-compact" onClick={onAdd} title="Add workspace">
          + Add
        </button>
        <PaneHideButton pane="workspaces" onHide={onHide} disabled={hideDisabled} />
      </div>
      <div className="scroll-list">
        <button
          type="button"
          className={`workspace-btn list-btn ${selected === "all" ? "active" : ""}`}
          onClick={() => onSelect("all")}
        >
          <span className="workspace-icon workspace-icon--all" aria-hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="7" height="7" rx="1.5" />
              <rect x="14" y="3" width="7" height="7" rx="1.5" />
              <rect x="3" y="14" width="7" height="7" rx="1.5" />
              <rect x="14" y="14" width="7" height="7" rx="1.5" />
            </svg>
          </span>
          <span className="workspace-copy">
            <span className="workspace-name">All workspaces</span>
            <span className="workspace-meta">Every repo</span>
          </span>
          <span className="count-pill">{allTicketCount}</span>
        </button>
        {workspaces.map((w) => (
          <button
            key={w.id}
            type="button"
            className={`workspace-btn list-btn ${selected === w.slug ? "active" : ""}`}
            onClick={() => onSelect(w.slug)}
          >
            <span
              className="workspace-icon"
              style={{ background: "rgba(111,174,143,.14)", color: "var(--ac2)" }}
              aria-hidden
            >
              {w.name.charAt(0).toUpperCase()}
            </span>
            <span className="workspace-copy">
              <span className="workspace-name">{w.name}</span>
              <span className="workspace-meta">
                {w.workflow_template_slug || "No workflow"}
                {!w.repo_exists ? " · repo missing" : ""}
              </span>
            </span>
            {w.blocked_count > 0 ? (
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--red)", flex: "none" }} />
            ) : null}
            <span className="count-pill">{w.ticket_count}</span>
          </button>
        ))}
        {archivedCount > 0 && (
          <Link className="workspace-meta" style={{ display: "block", padding: "8px 10px" }} to={workspacesPath("workspaces")}>
            {archivedCount} archived — manage on the Workspaces page
          </Link>
        )}
        {selected !== "all" && workflowTemplates && (
          <div style={{ padding: "8px 4px 0" }}>
            <div className="state-label" style={{ marginBottom: 6 }}>
              Workflow template
            </div>
            <select
              className="btn-secondary"
              style={{ width: "100%", fontSize: 12 }}
              aria-label="Workspace workflow template"
              value={workspaces.find((w) => w.slug === selected)?.workflow_template_slug ?? ""}
              onChange={(e) => setTemplate.mutate({ slug: selected, template: e.target.value })}
            >
              {workflowTemplates.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.name} ({t.stage_count} stages)
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
    </div>
  );
}
