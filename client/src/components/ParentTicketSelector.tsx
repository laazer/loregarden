import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, type TicketTreeNode, type WorkItemType } from "../api/client";
import {
  filterTreeEligibleParents,
  findTicketTreeNode,
  parentTypesForChild,
} from "../lib/parentTicketTree";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { collectExpandableIds, findAncestorIds, TicketTree } from "./TicketTree";

export interface ParentTicketSelection {
  id: string;
  external_id: string;
  title: string;
  work_item_type: WorkItemType;
}

interface ParentTicketSelectorProps {
  workspaceSlug: string;
  value: string | null;
  onChange: (parentId: string | null, parent?: ParentTicketSelection | null) => void;
  childWorkItemType?: WorkItemType | null;
  allowNone?: boolean;
  noneLabel?: string;
  disabled?: boolean;
  label?: string;
  hint?: string;
  placeholder?: string;
}

function toSelection(node: TicketTreeNode): ParentTicketSelection {
  return {
    id: node.id,
    external_id: node.external_id,
    title: node.title,
    work_item_type: node.work_item_type,
  };
}

export function ParentTicketSelector({
  workspaceSlug,
  value,
  onChange,
  childWorkItemType = null,
  allowNone = true,
  noneLabel = "None (no parent)",
  disabled = false,
  label = "Parent",
  hint,
  placeholder = "Choose a parent work item…",
}: ParentTicketSelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(value);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const allowedTypes = useMemo(() => parentTypesForChild(childWorkItemType), [childWorkItemType]);

  const treeQuery = useQuery({
    queryKey: ["ticket-tree", workspaceSlug, "parent-selector"],
    queryFn: () => api.ticketTree({ workspace: workspaceSlug }),
    enabled: !!workspaceSlug,
  });

  const searchQuery = useQuery({
    queryKey: ["ticket-tree", workspaceSlug, "parent-selector-search", search.trim()],
    queryFn: () =>
      api.ticketTree({
        workspace: workspaceSlug,
        search: search.trim() || undefined,
      }),
    enabled: !!workspaceSlug && open,
  });

  const baseTree = treeQuery.data ?? [];
  const activeTree = search.trim() ? (searchQuery.data ?? []) : baseTree;

  const filteredTree = useMemo(
    () => filterTreeEligibleParents(activeTree, allowedTypes),
    [activeTree, allowedTypes],
  );

  const selectedNode = useMemo(
    () => (value ? findTicketTreeNode(baseTree, value) : null),
    [baseTree, value],
  );

  const pendingNode = useMemo(
    () => (pendingId ? findTicketTreeNode(filteredTree, pendingId) : null),
    [filteredTree, pendingId],
  );

  const selectablePending = pendingNode && allowedTypes.has(pendingNode.work_item_type);

  useEffect(() => {
    if (!open) return;
    setPendingId(value);
    setSearch("");
  }, [open, value]);

  useEffect(() => {
    if (!open) return;
    if (search.trim()) {
      setExpandedIds(new Set(collectExpandableIds(filteredTree)));
      return;
    }
    if (value) {
      setExpandedIds(new Set(findAncestorIds(baseTree, value)));
      return;
    }
    setExpandedIds(new Set(collectExpandableIds(filteredTree).slice(0, 8)));
  }, [open, search, value, filteredTree, baseTree]);

  const displayValue = selectedNode
    ? `${selectedNode.external_id} · ${selectedNode.title}`
    : allowNone
      ? noneLabel
      : placeholder;

  const childLabel = childWorkItemType ? workItemTypeLabel(childWorkItemType).toLowerCase() : "work item";
  const allowedLabel = [...allowedTypes].map((type) => workItemTypeLabel(type).toLowerCase()).join(", ");

  const confirmSelection = () => {
    if (!selectablePending || !pendingNode) return;
    onChange(pendingNode.id, toSelection(pendingNode));
    setOpen(false);
  };

  const clearSelection = () => {
    if (!allowNone) return;
    onChange(null, null);
    setOpen(false);
  };

  return (
    <>
      <div className="modal-field" style={{ marginBottom: 0 }}>
        {label ? <div className="modal-field-label">{label}</div> : null}
        <button
          type="button"
          className="parent-ticket-selector-trigger btn-secondary"
          disabled={disabled || !workspaceSlug}
          onClick={() => setOpen(true)}
        >
          <span className={`parent-ticket-selector-value${selectedNode ? "" : " parent-ticket-selector-placeholder"}`}>
            {displayValue}
          </span>
          <span className="parent-ticket-selector-action">Browse</span>
        </button>
        {hint ? <p className="modal-hint" style={{ margin: "6px 0 0" }}>{hint}</p> : null}
      </div>

      {open && (
        <>
          <div className="modal-overlay" onClick={() => setOpen(false)} role="presentation" />
          <div
            className="modal-panel modal-panel-wide parent-ticket-selector-modal"
            role="dialog"
            aria-labelledby="parent-ticket-selector-title"
          >
            <div className="modal-header">
              <div>
                <div className="state-label">{workspaceSlug}</div>
                <h2 id="parent-ticket-selector-title" className="modal-title">
                  Select parent
                </h2>
                <p className="modal-subtitle">
                  Choose a {allowedLabel} to contain this {childLabel}.
                </p>
              </div>
              <button type="button" className="btn-secondary modal-close-btn" onClick={() => setOpen(false)}>
                ✕
              </button>
            </div>

            <div className="modal-body" style={{ gap: 12 }}>
              <input
                className="btn-secondary ticket-search"
                style={{ width: "100%", boxSizing: "border-box" }}
                value={search}
                placeholder="Search tickets by title or id…"
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
              />

              <div className="parent-ticket-selector-tree">
                {treeQuery.isLoading || (search.trim() && searchQuery.isLoading) ? (
                  <p className="modal-hint">Loading ticket tree…</p>
                ) : filteredTree.length === 0 ? (
                  <p className="modal-hint">
                    {search.trim()
                      ? "No matching parents found."
                      : `No valid parent ${allowedLabel} in this workspace.`}
                  </p>
                ) : (
                  <TicketTree
                    nodes={filteredTree}
                    selectedId={pendingId}
                    expandedIds={expandedIds}
                    showExternalId
                    onSelect={(id) => {
                      const node = findTicketTreeNode(filteredTree, id);
                      if (node && allowedTypes.has(node.work_item_type)) {
                        setPendingId(id);
                      } else {
                        setPendingId(id);
                        setExpandedIds((current) => {
                          const next = new Set(current);
                          next.add(id);
                          return next;
                        });
                      }
                    }}
                    onToggle={(id) => {
                      setExpandedIds((current) => {
                        const next = new Set(current);
                        if (next.has(id)) next.delete(id);
                        else next.add(id);
                        return next;
                      });
                    }}
                  />
                )}
              </div>

              {pendingNode && (
                <div className="state-card" style={{ fontSize: 12 }}>
                  <div className="state-label">Selected</div>
                  <div>
                    {pendingNode.external_id} · {pendingNode.title}
                  </div>
                  {!allowedTypes.has(pendingNode.work_item_type) && (
                    <p className="modal-hint" style={{ color: "var(--rdl)", margin: "6px 0 0" }}>
                      This {workItemTypeLabel(pendingNode.work_item_type).toLowerCase()} cannot be a parent for a{" "}
                      {childLabel}.
                    </p>
                  )}
                </div>
              )}
            </div>

            <div className="modal-footer">
              {allowNone && (
                <button type="button" className="btn-secondary" onClick={clearSelection}>
                  {noneLabel}
                </button>
              )}
              <div style={{ flex: 1 }} />
              <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!selectablePending}
                onClick={confirmSelection}
              >
                Select parent
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
