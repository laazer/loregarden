/** Where a reference id actually opens in Workflow Studio.
 *
 * AC7 of lg-milestone-that-1239 asks the refusal to name every referencing
 * workflow or template "as a link to Studio → Workflows → that slug, so the
 * operator can go remove the stage". Studio → Workflows addresses **drafts**,
 * and the scan reports `workflow_templates.slug`, so the two are not the same
 * string: on the live database the draft `blobert-ddd` publishes the template
 * `studio-blobert-ddd`, and that template is what names both of the two agents
 * whose Delete button renders. Navigating to the template id lands on a blank
 * Untitled form with a 404 behind it — 100% of what this install produces.
 */

import {
  isNavigableKind,
  readAgentDeleteRefusal,
  resolveWorkflowDestination,
} from "../agentDeleteRefusal";

/** The live shape: one draft, publishing a template under a prefixed slug. */
const DRAFTS = [
  { slug: "blobert-ddd", published_template_slug: "studio-blobert-ddd" },
  { slug: "loregarden-v3", published_template_slug: "" },
];

describe("resolveWorkflowDestination", () => {
  it("maps a template slug to the draft that published it", () => {
    expect(resolveWorkflowDestination("workflow_template", "studio-blobert-ddd", DRAFTS)).toBe(
      "blobert-ddd",
    );
  });

  it("has no destination for a template no draft published", () => {
    // A hand-authored template is editable nowhere in Workflow Studio, so the
    // id is reported as text. Returning its own slug would send the operator to
    // the new-workflow form, which is the defect this function exists to close.
    expect(resolveWorkflowDestination("workflow_template", "loregarden-v3", DRAFTS)).toBeNull();
  });

  it("does not match a draft that publishes nothing against an empty id", () => {
    expect(resolveWorkflowDestination("workflow_template", "", DRAFTS)).toBeNull();
  });

  it("opens a draft reference at its own slug", () => {
    expect(resolveWorkflowDestination("studio_workflow", "blobert-ddd", DRAFTS)).toBe(
      "blobert-ddd",
    );
  });

  it("has no destination for a draft that is not in the list", () => {
    // The editor's guard effect redirects a slug absent from the draft list, so
    // a control for one is a control that loses the refusal being read.
    expect(resolveWorkflowDestination("studio_workflow", "gone", DRAFTS)).toBeNull();
  });

  it("has no destination for a class that names no workflow at all", () => {
    for (const kind of ["ticket_next_agent", "agent_run", "mcp_tool_call"]) {
      expect(resolveWorkflowDestination(kind, "blobert-ddd", DRAFTS)).toBeNull();
      expect(isNavigableKind(kind)).toBe(false);
    }
  });

  it("has no destination while the draft list is still empty", () => {
    expect(resolveWorkflowDestination("workflow_template", "studio-blobert-ddd", [])).toBeNull();
  });
});

describe("readAgentDeleteRefusal", () => {
  it("reads both halves off the error body", () => {
    const refusal = readAgentDeleteRefusal({
      payload: {
        detail: "Cannot delete agent 'godot-ddd-migrator'.",
        references: {
          blocking: [{ kind: "workflow_template", ids: ["studio-blobert-ddd"], count: 1 }],
          historical: [{ kind: "agent_run", ids: [], count: 24 }],
        },
      },
    });

    expect(refusal?.references.blocking[0].kind).toBe("workflow_template");
    expect(refusal?.references.historical[0].count).toBe(24);
  });

  it("is null for a failure that carried no references", () => {
    // A 500, a dropped connection, the 404 of an agent somebody else removed: a
    // notice listing no references reads as "nothing is in the way".
    expect(readAgentDeleteRefusal({ payload: { detail: "boom" } })).toBeNull();
    expect(readAgentDeleteRefusal(new Error("offline"))).toBeNull();
  });
});
