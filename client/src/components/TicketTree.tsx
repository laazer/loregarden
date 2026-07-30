import type { ReactNode } from "react";

import type { TicketTreeNode } from "../api/client";
import {
  TICKET_STATE_COLORS,
  TICKET_STATE_LABELS,
  stageStatusColor,
} from "../lib/ticketStates";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import { PrioBars } from "./PrioBars";
import {
  TicketCardBody,
  childProgressSegments,
} from "./chat/primitives/TicketCardMeta";
import "./chat/primitives/PrimitiveCard.css";

function TreeRowTrail({
  node,
  workflowRunning,
  showAddChild,
  onAddChild,
  renderRowAction,
}: {
  node: TicketTreeNode;
  workflowRunning: boolean;
  showAddChild: boolean;
  onAddChild?: (node: TicketTreeNode) => void;
  renderRowAction?: (node: TicketTreeNode) => ReactNode;
}) {
  const hasChildren = node.children.length > 0;
  return (
    <div className="tree-row-trail">
      {workflowRunning && (
        <span
          className="tree-workflow-dot running"
          title="Workflow running"
          aria-label="Workflow running"
        />
      )}
      {hasChildren && (
        <span className="count-pill tree-child-count">{node.child_count}</span>
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
      {renderRowAction?.(node)}
    </div>
  );
}

function TreeRow({
  node,
  selectedId,
  expandedIds,
  onSelect,
  onToggle,
  onAddChild,
  renderRowAction,
  showExternalId = false,
  presentation = "default",
  depth = 0,
}: {
  node: TicketTreeNode;
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onAddChild?: (node: TicketTreeNode) => void;
  renderRowAction?: (node: TicketTreeNode) => ReactNode;
  showExternalId?: boolean;
  presentation?: "default" | "v6";
  depth?: number;
}) {
  const hasChildren = node.children.length > 0;
  const expanded = expandedIds.has(node.id);
  const isSelected = selectedId === node.id;
  const workflowRunning = node.workflow_stage_status === "running";
  const showAddChild = !!onAddChild && canHaveChildren(node.work_item_type);
  const stateColor = TICKET_STATE_COLORS[node.state];
  const wfColor = stageStatusColor(node.workflow_stage_status);
  const isV6 = presentation === "v6";

  const showTrail = workflowRunning || showAddChild || Boolean(renderRowAction) || hasChildren;
  const childProgress = hasChildren ? childProgressSegments(node.children) : null;

  const handleRowClick = () => {
    onSelect(node.id);
    if (hasChildren) onToggle(node.id);
  };

  return (
    <div className="tree-node">
      <div
        className={[
          "tree-row",
          "list-btn",
          isV6 ? "tree-row--v6" : null,
          isSelected ? "active" : null,
        ]
          .filter(Boolean)
          .join(" ")}
        style={{
          borderLeft: isV6 ? undefined : `2px solid ${stateColor}`,
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
        {isV6 ? (
          <>
            <div className="tree-row-v6-body">
              <TicketCardBody
                title={node.title}
                externalId={showExternalId ? node.external_id : undefined}
                priority={node.priority}
                state={node.state}
                workspaceSlug={node.workspace_slug}
                stageName={node.workflow_stage_name || undefined}
                stageStatus={node.workflow_stage_status}
                segments={childProgress?.segments ?? []}
                progressLabel={
                  childProgress?.total
                    ? `${childProgress.done}/${childProgress.total}`
                    : null
                }
                underPriority={
                  hasChildren ? (
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
                  ) : null
                }
              />
            </div>
            {showTrail ? (
              <TreeRowTrail
                node={node}
                workflowRunning={workflowRunning}
                showAddChild={showAddChild}
                onAddChild={onAddChild}
                renderRowAction={renderRowAction}
              />
            ) : null}
          </>
        ) : (
          <>
            <div className={`tree-row-head${hasChildren ? "" : " tree-row-head--no-chevron"}`}>
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
              ) : null}
              <PrioBars priority={node.priority} />
              <div className="tree-card-title">
                {showExternalId ? (
                  <>
                    <span className="tree-external-id">{node.external_id}</span>
                    <span className="tree-title-sep"> · </span>
                    {node.title}
                  </>
                ) : (
                  node.title
                )}
              </div>
            </div>
            <div className="tree-card-meta">
              <div className="tree-card-meta-main">
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    fontWeight: 500,
                    color: stateColor,
                  }}
                >
                  <span className="tree-state-dot" style={{ background: stateColor }} />
                  {TICKET_STATE_LABELS[node.state]}
                </span>
                {node.workspace_slug ? (
                  <>
                    <span style={{ color: "var(--bd2)" }}>·</span>
                    <span
                      style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}
                    >
                      {node.workspace_slug}
                    </span>
                  </>
                ) : null}
              </div>
              {showTrail ? (
                <TreeRowTrail
                  node={node}
                  workflowRunning={workflowRunning}
                  showAddChild={showAddChild}
                  onAddChild={onAddChild}
                  renderRowAction={renderRowAction}
                />
              ) : null}
            </div>
            {node.workflow_stage_name ? (
              <div className="tree-card-workflow">
                <span className="tree-workflow-dot-inline" style={{ background: wfColor }} />
                <span style={{ color: wfColor, fontWeight: 500 }}>{node.workflow_stage_name}</span>
                <span style={{ color: "var(--txl)" }}>
                  {node.workflow_stage_status.replace("_", " ")}
                </span>
              </div>
            ) : null}
          </>
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
            onAddChild={onAddChild}
            renderRowAction={renderRowAction}
            showExternalId={showExternalId}
            presentation={presentation}
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
  renderRowAction?: (node: TicketTreeNode) => ReactNode;
  showExternalId?: boolean;
  presentation?: "default" | "v6";
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
  renderRowAction,
  showExternalId = false,
  presentation = "default",
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
          renderRowAction={renderRowAction}
          showExternalId={showExternalId}
          presentation={presentation}
          depth={depth}
        />
      ))}
    </div>
  );
}
