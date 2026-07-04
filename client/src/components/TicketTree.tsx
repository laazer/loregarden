import type { CSSProperties } from "react";
import type { TicketState, TicketTreeNode, WorkItemType } from "../api/client";

const STATE_COLORS: Record<TicketState, string> = {
  backlog: "var(--txm)",
  in_progress: "var(--blue)",
  blocked: "var(--red)",
  done: "var(--grn)",
  wont_do: "var(--amb)",
};

const TYPE_COLORS: Record<WorkItemType, string> = {
  milestone: "var(--ac)",
  feature: "var(--blue)",
  capability: "var(--amb)",
  task: "var(--txm)",
  bug: "var(--red)",
};

const TYPE_LABELS: Record<WorkItemType, string> = {
  milestone: "MS",
  feature: "FE",
  capability: "CP",
  task: "TK",
  bug: "BG",
};

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

interface TicketTreeProps {
  nodes: TicketTreeNode[];
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  depth?: number;
}

function TreeRow({
  node,
  selectedId,
  expandedIds,
  onSelect,
  onToggle,
  depth = 0,
}: {
  node: TicketTreeNode;
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  depth?: number;
}) {
  const hasChildren = node.children.length > 0;
  const expanded = expandedIds.has(node.id);
  const isLeaf = node.work_item_type === "task" || node.work_item_type === "bug";
  const isSelected = selectedId === node.id;

  const handleRowClick = () => {
    onSelect(node.id);
    if (hasChildren) onToggle(node.id);
  };

  return (
    <div className="tree-node" style={{ "--tree-depth": depth } as CSSProperties}>
      <div
        className={`tree-row list-btn ${isSelected ? "active" : ""}`}
        style={{
          borderLeft: `2px solid ${isLeaf ? STATE_COLORS[node.state] : TYPE_COLORS[node.work_item_type]}`,
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
              <span className={`tree-chevron-icon ${expanded ? "expanded" : ""}`} />
            </button>
          ) : (
            <span className="tree-chevron-btn tree-chevron-spacer" aria-hidden />
          )}
          <span
            className="type-badge"
            style={{
              background: `${TYPE_COLORS[node.work_item_type]}22`,
              color: TYPE_COLORS[node.work_item_type],
            }}
          >
            {TYPE_LABELS[node.work_item_type]}
          </span>
          <span className="tree-title">{node.title}</span>
          {hasChildren && (
            <span className="count-pill tree-child-count">{node.child_count}</span>
          )}
        </div>
        {isLeaf && (
          <div className="tree-meta">
            <span style={{ color: STATE_COLORS[node.state] }}>{node.state.replace("_", " ")}</span>
            {node.workflow_stage_name && (
              <>
                <span className="tree-dot">·</span>
                <span>{node.workflow_stage_name}</span>
              </>
            )}
          </div>
        )}
      </div>
      {hasChildren && expanded && (
        <div className="tree-children" role="group">
          <TicketTree
            nodes={node.children}
            selectedId={selectedId}
            expandedIds={expandedIds}
            onSelect={onSelect}
            onToggle={onToggle}
            depth={depth + 1}
          />
        </div>
      )}
    </div>
  );
}

export function TicketTree({
  nodes,
  selectedId,
  expandedIds,
  onSelect,
  onToggle,
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
          depth={depth}
        />
      ))}
    </div>
  );
}

export { TYPE_COLORS, TYPE_LABELS };
