import {
  applyMilestoneToTicket,
  applyParentToTicket,
  buildImportMilestoneOptions,
  buildImportParentOptions,
  buildQuickImportItem,
  collectImportExternalIds,
  formatAcceptanceCriteriaText,
  parseAcceptanceCriteriaText,
  importTicketHasParent,
  importTicketNeedsParent,
  validateImportDraft,
} from "../importTicketPreview";
import type { TicketImportItem, TicketSummary } from "../../api/client";

describe("importTicketPreview", () => {
  const existingMilestones: TicketSummary[] = [
    {
      id: "ms-1",
      external_id: "m01-bootstrap",
      title: "Bootstrap",
      state: "backlog",
      priority: 2,
      workspace_slug: "loregarden",
      workflow_stage_key: "planning",
      workflow_stage_status: "pending",
      workflow_stage_name: "Planning",
      run_code: "",
      work_item_type: "milestone",
      parent_ticket_id: null,
      milestone: "01_milestone_bootstrap",
      branch: "",
      child_count: 0,
    },
  ];

  it("builds milestone options from existing and import batch", () => {
    const batch: TicketImportItem[] = [
      { title: "New MS", work_item_type: "milestone", external_id: "m02-new" },
      { title: "Task", work_item_type: "task", external_id: "t01" },
    ];
    const options = buildImportMilestoneOptions(existingMilestones, batch);
    expect(options).toHaveLength(2);
    expect(options.map((option) => option.external_id)).toEqual(["m01-bootstrap", "m02-new"]);
  });

  it("assigns milestone parent for features", () => {
    const ticket: TicketImportItem = {
      title: "Feature",
      work_item_type: "feature",
    };
    const updated = applyMilestoneToTicket(ticket, {
      id: "ms-1",
      external_id: "m01-bootstrap",
      title: "Bootstrap",
      milestone: "01_milestone_bootstrap",
      source: "existing",
    });
    expect(updated.parent_ticket_id).toBe("ms-1");
    expect(updated.milestone).toBe("01_milestone_bootstrap");
  });

  it("assigns parent by external id for import batch parents", () => {
    const ticket: TicketImportItem = {
      title: "Task",
      work_item_type: "task",
    };
    const updated = applyParentToTicket(ticket, {
      id: null,
      external_id: "cap-01",
      label: "cap-01 · Capability (import)",
      source: "import",
    });
    expect(updated.parent_external_id).toBe("cap-01");
  });

  it("validates missing parents", () => {
    const issues = validateImportDraft([
      { title: "Orphan task", work_item_type: "task" },
    ]);
    expect(issues).toHaveLength(1);
    expect(importTicketHasParent({ title: "Ok", work_item_type: "milestone" })).toBe(false);
    expect(importTicketNeedsParent("task")).toBe(true);
  });

  it("validates empty titles and parses acceptance criteria", () => {
    expect(validateImportDraft([{ title: "  ", work_item_type: "milestone" }])).toEqual([
      "Ticket: title is required",
    ]);
    expect(formatAcceptanceCriteriaText(["One", "Two"])).toBe("One\nTwo");
    expect(parseAcceptanceCriteriaText("- One\n* Two\nThree")).toEqual(["One", "Two", "Three"]);
  });

  it("lists capability parents for tasks", () => {
    const options = buildImportParentOptions(
      { title: "Task", work_item_type: "task" },
      existingMilestones,
      [{ title: "Cap", work_item_type: "capability", external_id: "cap-01" }],
    );
    expect(options).toHaveLength(1);
    expect(options[0].external_id).toBe("cap-01");
  });

  it("creates quick import items with unique external ids", () => {
    const existing = collectImportExternalIds(existingMilestones, []);
    const milestone = buildQuickImportItem({
      work_item_type: "milestone",
      title: "Sprint Two",
      existingExternalIds: existing,
    });
    expect(milestone.external_id).toBe("m-sprint-two");
    expect(milestone.source_format).toBe("quick");

    const capability = buildQuickImportItem({
      work_item_type: "capability",
      title: "Import Tools",
      existingExternalIds: collectImportExternalIds(existingMilestones, [milestone]),
      parent_external_id: "f-backend",
    });
    expect(capability.work_item_type).toBe("capability");
    expect(capability.parent_external_id).toBe("f-backend");
  });
});
