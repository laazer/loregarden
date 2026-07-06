import type { TicketTreeNode, WorkItemType } from "../api/client";
import { allowedParentTypes, canHaveChildren } from "./workItemHierarchy";

export function findTicketTreeNode(nodes: TicketTreeNode[], ticketId: string): TicketTreeNode | null {
  for (const node of nodes) {
    if (node.id === ticketId) return node;
    const child = findTicketTreeNode(node.children, ticketId);
    if (child) return child;
  }
  return null;
}

export function filterTreeEligibleParents(
  nodes: TicketTreeNode[],
  allowedTypes: Set<WorkItemType>,
): TicketTreeNode[] {
  const result: TicketTreeNode[] = [];
  for (const node of nodes) {
    const filteredChildren = filterTreeEligibleParents(node.children, allowedTypes);
    const isValid = allowedTypes.has(node.work_item_type);
    if (isValid || filteredChildren.length > 0) {
      result.push({ ...node, children: filteredChildren, child_count: filteredChildren.length });
    }
  }
  return result;
}

export function parentTypesForChild(childWorkItemType?: WorkItemType | null): Set<WorkItemType> {
  if (!childWorkItemType) {
    return new Set(
      (["milestone", "feature", "capability"] as WorkItemType[]).filter((type) => canHaveChildren(type)),
    );
  }
  return new Set(allowedParentTypes(childWorkItemType));
}
