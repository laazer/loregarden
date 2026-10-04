import {
  gateStudioPath,
  gateStudioTargetFromPath,
  type GateStudioTarget,
} from "../appNavigation";

const targets: GateStudioTarget[] = [
  { workspaceSlug: null, workflowSlug: null, controlId: null },
  { workspaceSlug: "blobert", workflowSlug: null, controlId: null },
  { workspaceSlug: "blobert", workflowSlug: "blobert-tdd", controlId: null },
  { workspaceSlug: "blobert", workflowSlug: "blobert-tdd", controlId: "gate-stage:a%3Ab" },
  { workspaceSlug: "blobert", workflowSlug: null, controlId: "transition-command:0" },
];

describe("Gate Studio paths", () => {
  it.each(targets)("round-trip %o through the URL", (target) => {
    expect(gateStudioTargetFromPath(gateStudioPath(target))).toEqual(target);
  });

  it("uses '_' for workspace-wide controls", () => {
    expect(
      gateStudioPath({ workspaceSlug: "blobert", workflowSlug: null, controlId: "transition-script" }),
    ).toBe("/studio/gates/blobert/_/transition-script");
  });

  it("reads a bare '%' as a miss to show, not a crash", () => {
    expect(gateStudioTargetFromPath("/studio/gates/%").workspaceSlug).toBe("%");
  });

  it("is empty for a path that is not Gate Studio", () => {
    expect(gateStudioTargetFromPath("/studio/agents/x")).toEqual({
      workspaceSlug: null,
      workflowSlug: null,
      controlId: null,
    });
  });
});
