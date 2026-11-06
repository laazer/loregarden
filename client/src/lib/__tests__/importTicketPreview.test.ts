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
import { MAX_ACCEPTANCE_CRITERIA } from "../criterionDrafts";
import type { TicketImportItem, TicketSummary } from "../../api/client";

function criteria(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `criterion ${i}`);
}

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

  // The import is a batch: one over-cap row must hold the whole batch client-side
  // with the offending row named, rather than letting the server refuse it
  // mid-import and leave half the tickets created.
  it("an over-cap row is held with its label and count named", () => {
    const over = MAX_ACCEPTANCE_CRITERIA + 3;
    const issues = validateImportDraft([
      {
        title: "Too much work for one ticket",
        work_item_type: "milestone",
        source_label: "batch.md",
        acceptance_criteria: criteria(over),
      },
    ]);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("batch.md");
    expect(issues[0]).toContain(String(over));
    expect(issues[0]).toContain(String(MAX_ACCEPTANCE_CRITERIA));
  });

  // The batch's criteria come out of `parseAcceptanceCriteriaText`, which drops
  // blanks and bullet markers — and the server's `normalize_criteria` drops them
  // again. A cap check counting the raw array holds a batch the server would
  // take, and the operator has no way to see why: the count in the issue matches
  // nothing they can see on the row.
  it("blank rows do not push a row over the cap", () => {
    const withBlanks = [...criteria(MAX_ACCEPTANCE_CRITERIA), "   ", ""];
    expect(
      validateImportDraft([
        {
          title: "Twelve criteria and two blank lines",
          work_item_type: "milestone",
          source_label: "batch.md",
          acceptance_criteria: withBlanks,
        },
      ]),
    ).toEqual([]);
    // And the parse the field actually feeds it agrees.
    expect(parseAcceptanceCriteriaText("- One\r\n\r\n* Two\r\n   \r\nThree")).toEqual([
      "One",
      "Two",
      "Three",
    ]);
  });

  it("a row with no acceptance_criteria at all is not held", () => {
    // The field is optional on every import format, so `undefined.length` is the
    // crash the cap check adds to a batch that was importing fine.
    expect(
      validateImportDraft([{ title: "No criteria", work_item_type: "milestone" }]),
    ).toEqual([]);
  });

  it("a row at the cap is not held", () => {
    expect(
      validateImportDraft([
        {
          title: "Exactly at the cap",
          work_item_type: "milestone",
          source_label: "batch.md",
          acceptance_criteria: criteria(MAX_ACCEPTANCE_CRITERIA),
        },
      ]),
    ).toEqual([]);
  });

  // A milestone needs no parent, so the parent check `continue`s past it. The
  // cap check has to run before that early exit or an over-cap milestone row is
  // never looked at.
  it("a milestone row over the cap is still checked", () => {
    const issues = validateImportDraft([
      {
        title: "Oversized milestone",
        work_item_type: "milestone",
        source_label: "milestone.md",
        acceptance_criteria: criteria(MAX_ACCEPTANCE_CRITERIA + 1),
      },
    ]);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("milestone.md");
  });
});
