import type { TicketImportItem, TicketSummary, WorkItemType } from "../api/client";
import { slugify } from "./slugify";

export const IMPORT_REQUIRED_PARENT: Partial<Record<WorkItemType, WorkItemType>> = {
  feature: "milestone",
  capability: "feature",
  task: "capability",
  bug: "capability",
};

export const IMPORT_PARENT_TYPES: Partial<Record<WorkItemType, WorkItemType[]>> = {
  feature: ["milestone"],
  capability: ["feature"],
  task: ["capability"],
  bug: ["milestone", "feature", "capability"],
};

export function importParentTypes(type: WorkItemType): WorkItemType[] {
  return IMPORT_PARENT_TYPES[type] ?? [];
}

export interface ImportMilestoneOption {
  id: string | null;
  external_id: string;
  title: string;
  milestone: string;
  source: "existing" | "import" | "quick";
}

export interface ImportParentOption {
  id: string | null;
  external_id: string;
  label: string;
  source: "existing" | "import" | "quick";
}

const QUICK_EXTERNAL_PREFIX: Partial<Record<WorkItemType, string>> = {
  milestone: "m",
  feature: "f",
  capability: "c",
};

export function collectImportExternalIds(
  existing: TicketSummary[],
  batch: TicketImportItem[],
): Set<string> {
  const ids = new Set<string>();
  for (const ticket of existing) ids.add(ticket.external_id);
  for (const ticket of batch) {
    if (ticket.external_id) ids.add(ticket.external_id);
  }
  return ids;
}

export function nextQuickExternalId(
  workItemType: WorkItemType,
  title: string,
  existingExternalIds: Set<string>,
): string {
  const prefix = QUICK_EXTERNAL_PREFIX[workItemType] ?? "w";
  const slug = slugify(title) || "item";
  let candidate = `${prefix}-${slug}`;
  let suffix = 2;
  while (existingExternalIds.has(candidate)) {
    candidate = `${prefix}-${slug}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

export function buildQuickImportItem(params: {
  work_item_type: WorkItemType;
  title: string;
  existingExternalIds: Set<string>;
  parent_external_id?: string;
  parent_ticket_id?: string | null;
  milestone?: string;
}): TicketImportItem {
  const title = params.title.trim();
  if (!title) {
    throw new Error("Title is required");
  }

  const external_id = nextQuickExternalId(
    params.work_item_type,
    title,
    params.existingExternalIds,
  );

  return {
    title,
    work_item_type: params.work_item_type,
    external_id,
    description: "",
    acceptance_criteria: [],
    priority: 3,
    milestone:
      params.milestone ??
      (params.work_item_type === "milestone" ? external_id : ""),
    parent_external_id: params.parent_external_id ?? "",
    parent_ticket_id: params.parent_ticket_id ?? null,
    source_format: "quick",
    source_label: "(quick create)",
  };
}

export function milestoneOptionFromItem(item: TicketImportItem): ImportMilestoneOption {
  return {
    id: null,
    external_id: item.external_id ?? "",
    title: item.title,
    milestone: item.milestone?.trim() || item.external_id || "",
    source: item.source_format === "quick" ? "quick" : "import",
  };
}

export function parentOptionFromItem(item: TicketImportItem): ImportParentOption {
  const tag = item.source_format === "quick" ? " (new)" : " (import)";
  return {
    id: null,
    external_id: item.external_id ?? "",
    label: `${item.external_id} · ${item.title}${tag}`,
    source: item.source_format === "quick" ? "quick" : "import",
  };
}

export function buildImportFeatureOptions(
  existing: TicketSummary[],
  batch: TicketImportItem[],
): ImportParentOption[] {
  const options: ImportParentOption[] = [];

  for (const item of existing) {
    if (item.work_item_type !== "feature") continue;
    options.push({
      id: item.id,
      external_id: item.external_id,
      label: `${item.external_id} · ${item.title}`,
      source: "existing",
    });
  }

  for (const item of batch) {
    if (item.work_item_type !== "feature" || !item.external_id) continue;
    if (options.some((option) => option.external_id === item.external_id)) continue;
    options.push(parentOptionFromItem(item));
  }

  return options.sort((a, b) => a.label.localeCompare(b.label));
}

export function importTicketNeedsParent(type: WorkItemType): boolean {
  return type !== "milestone";
}

export function importTicketHasParent(ticket: TicketImportItem): boolean {
  return !!(ticket.parent_ticket_id?.trim() || ticket.parent_external_id?.trim());
}

export function buildImportMilestoneOptions(
  existing: TicketSummary[],
  batch: TicketImportItem[],
): ImportMilestoneOption[] {
  const options: ImportMilestoneOption[] = existing
    .filter((ticket) => ticket.work_item_type === "milestone")
    .map((ticket) => ({
      id: ticket.id,
      external_id: ticket.external_id,
      title: ticket.title,
      milestone: ticket.milestone || ticket.external_id,
      source: "existing" as const,
    }));

  for (const ticket of batch) {
    if (ticket.work_item_type !== "milestone" || !ticket.external_id) continue;
    if (options.some((option) => option.external_id === ticket.external_id)) continue;
    options.push({
      id: null,
      external_id: ticket.external_id,
      title: ticket.title,
      milestone: ticket.milestone?.trim() || ticket.external_id,
      source: ticket.source_format === "quick" ? "quick" : "import",
    });
  }

  return options.sort((a, b) => a.title.localeCompare(b.title));
}

export function buildImportParentOptions(
  ticket: TicketImportItem,
  existing: TicketSummary[],
  batch: TicketImportItem[],
): ImportParentOption[] {
  const allowedTypes = importParentTypes(ticket.work_item_type);
  if (allowedTypes.length === 0) return [];

  const options: ImportParentOption[] = [];
  for (const required of allowedTypes) {
    for (const item of existing) {
      if (item.work_item_type !== required) continue;
      if (options.some((option) => option.external_id === item.external_id)) continue;
      options.push({
        id: item.id,
        external_id: item.external_id,
        label: `${item.external_id} · ${item.title}`,
        source: "existing",
      });
    }
    for (const item of batch) {
      if (item.work_item_type !== required || !item.external_id) continue;
      if (options.some((option) => option.external_id === item.external_id)) continue;
      options.push({
        id: null,
        external_id: item.external_id,
        label: `${item.external_id} · ${item.title}${
          item.source_format === "quick" ? " (new)" : " (import)"
        }`,
        source: item.source_format === "quick" ? "quick" : "import",
      });
    }
  }

  return options.sort((a, b) => a.label.localeCompare(b.label));
}

export function applyMilestoneToTicket(
  ticket: TicketImportItem,
  milestone: ImportMilestoneOption,
): TicketImportItem {
  const next: TicketImportItem = {
    ...ticket,
    milestone: milestone.milestone || milestone.external_id,
  };

  if (ticket.work_item_type === "feature" || ticket.work_item_type === "bug") {
    if (milestone.id) {
      return { ...next, parent_ticket_id: milestone.id, parent_external_id: "" };
    }
    return { ...next, parent_external_id: milestone.external_id, parent_ticket_id: null };
  }

  return next;
}

export function applyParentToTicket(
  ticket: TicketImportItem,
  parent: ImportParentOption | null,
): TicketImportItem {
  if (!parent) {
    return { ...ticket, parent_ticket_id: null, parent_external_id: "" };
  }
  if (parent.id) {
    return { ...ticket, parent_ticket_id: parent.id, parent_external_id: "" };
  }
  return { ...ticket, parent_external_id: parent.external_id, parent_ticket_id: null };
}

export function validateImportDraft(tickets: TicketImportItem[]): string[] {
  const issues: string[] = [];
  for (const ticket of tickets) {
    const label = ticket.source_label || ticket.title.trim() || "Ticket";
    if (!ticket.title.trim()) {
      issues.push(`${label}: title is required`);
    }
    if (!importTicketNeedsParent(ticket.work_item_type)) continue;
    if (!importTicketHasParent(ticket)) {
      issues.push(`${label}: assign a parent work item before importing`);
    }
  }
  return issues;
}

export function formatAcceptanceCriteriaText(criteria: string[] | undefined): string {
  return (criteria ?? []).join("\n");
}

export function parseAcceptanceCriteriaText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim().replace(/^[-*]\s+/, ""))
    .filter(Boolean);
}

export function priorityLabel(priority: number | undefined): string {
  switch (priority) {
    case 1:
      return "P1 — High";
    case 2:
      return "P2 — Medium";
    case 3:
      return "P3 — Low";
    default:
      return "P3 — Low";
  }
}
