import type { WorkItemType } from "../api/client";

/** Mirrors server VALID_HIERARCHY — parent types that may contain children. */
export const VALID_HIERARCHY: Record<WorkItemType, WorkItemType[]> = {
  milestone: ["feature", "bug"],
  feature: ["capability", "bug"],
  capability: ["task", "bug"],
  task: [],
  bug: [],
};

const TYPE_LABELS: Record<WorkItemType, string> = {
  milestone: "Milestone",
  feature: "Feature",
  capability: "Capability",
  task: "Task",
  bug: "Bug",
};

export function allowedChildTypes(parentType: WorkItemType): WorkItemType[] {
  return VALID_HIERARCHY[parentType] ?? [];
}

export function canHaveChildren(parentType: WorkItemType): boolean {
  return allowedChildTypes(parentType).length > 0;
}

export function defaultChildType(parentType: WorkItemType): WorkItemType {
  const allowed = allowedChildTypes(parentType);
  if (allowed.length === 0) return "task";
  return allowed[0];
}

export function workItemTypeLabel(type: WorkItemType): string {
  return TYPE_LABELS[type];
}

export function addChildActionLabel(parentType: WorkItemType): string {
  const allowed = allowedChildTypes(parentType);
  if (allowed.length === 0) return "Add child";
  if (allowed.length === 1) return `Add ${TYPE_LABELS[allowed[0]].toLowerCase()}`;
  const names = allowed.map((t) => TYPE_LABELS[t].toLowerCase()).join(" or ");
  return `Add ${names}`;
}
