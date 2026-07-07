import type { CSSProperties } from "react";
import type { TicketState, TicketTreeNode, WorkItemType } from "../api/client";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import { PrioBars } from "./PrioBars";

const STATE_COLORS: Record<TicketState, string> = {
  backlog: "var(--txm)",
  in_progress: "var(--blue)",
  blocked: "var(--red)",
  done: "var(--grn)",
  wont_do: "var(--amb)",
};

const STATE_LABELS: Record<TicketState, string> = {
  backlog: "Backlog",
  in_progress: "In Progress",
  blocked: "Blocked",
  done: "Done",
  wont_do: "Won't do",
};

const WORKFLOW_STATUS_COLORS: Record<string, string> = {
  running: "var(--blue)",
  awaiting: "var(--amb)",
  blocked: "var(--red)",
  done: "var(--grn)",
  pending: "var(--txl)",
  wont_do: "var(--amb)",
};

function TreeRow({
  node,
  selectedId,
  expandedIds,
  onSelect,
  onToggle,
  onAddChild,
  showExternalId = false,
  depth = 0,
}: {
  node: TicketTreeNode;
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onAddChild?: (node: TicketTreeNode) => void;
  showExternalId?: boolean;
  depth?: number;
}) {
  const hasChildren = node.children.length > 0;
  const expanded = expandedIds.has(node.id);
  const isSelected = selectedId === node.id;
  const workflowRunning = node.workflow_stage_status === "running";
  const showAddChild = !!onAddChild && canHaveChildren(node.work_item_type);
  const stateColor = STATE_COLORS[node.state];
  const wfColor = WORKFLOW_STATUS_COLORS[node.workflow_stage_status] ?? "var(--txl)";

  const handleRowClick = () => {
    onSelect(node.id);
    if (hasChildren) onToggle(node.id);
  };

  return (
    <div className="tree-node" style={{ "--tree-depth": depth } as CSSProperties}>
      <div
        className={`tree-row list-btn ${isSelected ? "active" : ""}`}
        style={{
          borderLeft: `2px solid ${stateColor}`,
        }}
        onClick={handleRowClick}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            handleRowClick();
          }
        }}
        role="treeitem"
        aria-expanded={hasChildren ? expanded : undefined}
        tabIndex={0}
      >
        <div className="tree-row-main">
          {hasChildren ? (
            <button
              type="button"
              className="tree-chevron-btn"
              aria-label={expanded ? "Collapse" : "Expand"}
              onClick={(e) => {
                e.stopPropagation();
                onToggle(node.id);
              }}
            >
              <TreeExpandChevron expanded={expanded} />
            </button>
          ) : (
            <span className="tree-chevron-btn tree-chevron-spacer" aria-hidden />
          )}
          <PrioBars priority={node.priority} />
          <span className="tree-card-title">
            {showExternalId ? (
              <>
                <span className="tree-external-id">{node.external_id}</span>
                <span className="tree-title-sep"> · </span>
                {node.title}
              </>
            ) : (
              node.title
            )}
          </span>
          <div className="tree-row-trail">
            {workflowRunning && (
              <span
                className="tree-workflow-dot running"
                title="Workflow running"
                aria-label="Workflow running"
              />
            )}
            {showAddChild && (
              <button
                type="button"
                className="tree-add-child-btn"
                title={addChildActionLabel(node.work_item_type)}
                aria-label={addChildActionLabel(node.work_item_type)}
                onClick={(e) => {
                  e.stopPropagation();
                  onAddChild?.(node);
                }}
              >
                +
              </button>
            )}
            {hasChildren && (
              <span className="count-pill tree-child-count">{node.child_count}</span>
            )}
          </div>
        </div>
        <div className="tree-card-meta">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontWeight: 500, color: stateColor }}>
            <span className="tree-state-dot" style={{ background: stateColor }} />
            {STATE_LABELS[node.state]}
          </span>
          {node.workspace_slug ? (
            <>
              <span style={{ color: "var(--bd2)" }}>·</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
                {node.workspace_slug}
              </span>
            </>
          ) : null}
        </div>
        {node.workflow_stage_name ? (
          <div className="tree-card-workflow">
            <span className="tree-workflow-dot-inline" style={{ background: wfColor }} />
            <span style={{ color: wfColor, fontWeight: 500 }}>{node.workflow_stage_name}</span>
            <span style={{ color: "var(--txl)" }}>{node.workflow_stage_status.replace("_", " ")}</span>
          </div>
        ) : null}
      </div>
      {hasChildren && expanded && (
        <div className="tree-children" role="group">
          <TicketTree
            nodes={node.children}
            selectedId={selectedId}
            expandedIds={expandedIds}
            onSelect={onSelect}
            onToggle={onToggle}
            onAddChild={onAddChild}
            showExternalId={showExternalId}
            depth={depth + 1}
          />
        </div>
      )}
    </div>
  );
}

interface TicketTreeProps {
  nodes: TicketTreeNode[];
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onAddChild?: (node: TicketTreeNode) => void;
  showExternalId?: boolean;
  depth?: number;
}

export function findAncestorIds(nodes: TicketTreeNode[], targetId: string): string[] {
  function walk(items: TicketTreeNode[], ancestors: string[]): string[] | null {
    for (const node of items) {
      if (node.id === targetId) return ancestors;
      const found = walk(node.children, [...ancestors, node.id]);
      if (found) return found;
    }
    return null;
  }
  return walk(nodes, []) ?? [];
}

export function collectExpandableIds(nodes: TicketTreeNode[]): string[] {
  const ids: string[] = [];
  for (const n of nodes) {
    if (n.children.length > 0) {
      ids.push(n.id);
      ids.push(...collectExpandableIds(n.children));
    }
  }
  return ids;
}

export function TicketTree({
  nodes,
  selectedId,
  expandedIds,
  onSelect,
  onToggle,
  onAddChild,
  showExternalId = false,
  depth = 0,
}: TicketTreeProps) {
  return (
    <div className="ticket-tree" role={depth === 0 ? "tree" : undefined}>
      {nodes.map((node) => (
        <TreeRow
          key={node.id}
          node={node}
          selectedId={selectedId}
          expandedIds={expandedIds}
          onSelect={onSelect}
          onToggle={onToggle}
          onAddChild={onAddChild}
          showExternalId={showExternalId}
          depth={depth}
        />
      ))}
    </div>
  );
}
