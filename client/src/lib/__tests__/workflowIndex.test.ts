import type { StudioWorkflow, WorkflowUsageStats } from "../../api/types";
import { isUnused, sortWorkflowRows, workflowIndexRows } from "../workflowIndex";

function workflow(slug: string, extra: Partial<StudioWorkflow> = {}): StudioWorkflow {
  return {
    slug,
    name: slug,
    description: "",
    stages: [],
    transitions: [],
    published_template_id: `tpl-${slug}`,
    published_template_slug: slug,
    ...extra,
  } as StudioWorkflow;
}

function use(template_slug: string, tickets: number, open = 0): WorkflowUsageStats {
  return { template_slug, tickets, open, last_used_at: tickets ? "2026-10-28T12:00:00" : null };
}

describe("workflowIndex", () => {
  it("counts a Studio workflow's tickets on the template it publishes to", () => {
    const v3 = workflow("loregarden-tdd-v3", { published_template_slug: "studio-loregarden-tdd-v3" });
    const [row] = workflowIndexRows([v3], [use("studio-loregarden-tdd-v3", 1579, 1183)]);
    expect(row).toMatchObject({ tickets: 1579, open: 1183, kind: "custom" });
  });

  it("names a never-published Studio workflow a draft, and a seeded template built-in", () => {
    const rows = workflowIndexRows(
      [
        workflow("draft-flow", { published_template_id: null, published_template_slug: "" }),
        workflow("spike", { built_in: true }),
      ],
      [],
    );
    expect(rows.map((row) => row.kind)).toEqual(["draft", "built-in"]);
  });

  it("puts the most-used first, and marks the ones no ticket has used", () => {
    const rows = workflowIndexRows(
      [workflow("spike"), workflow("tdd"), workflow("extended-tdd")],
      [use("spike", 4), use("tdd", 256), use("extended-tdd", 0)],
    );
    const sorted = sortWorkflowRows(rows, "tickets", "descending");
    expect(sorted.map((row) => row.workflow.slug)).toEqual(["tdd", "spike", "extended-tdd"]);
    expect(sorted.map(isUnused)).toEqual([false, false, true]);
  });
});
