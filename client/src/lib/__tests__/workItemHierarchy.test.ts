import {
  addChildActionLabel,
  allowedChildTypes,
  allowedParentTypes,
  canHaveChildren,
  defaultChildType,
} from "../workItemHierarchy";

describe("workItemHierarchy", () => {
  it("allows milestone children", () => {
    expect(allowedChildTypes("milestone")).toEqual(["feature", "bug"]);
    expect(canHaveChildren("milestone")).toBe(true);
    expect(defaultChildType("milestone")).toBe("feature");
  });

  it("allows capability children", () => {
    expect(allowedChildTypes("capability")).toEqual(["task", "bug"]);
    expect(canHaveChildren("capability")).toBe(true);
  });

  it("disallows leaf types", () => {
    expect(canHaveChildren("task")).toBe(false);
    expect(canHaveChildren("bug")).toBe(false);
  });

  it("labels add actions", () => {
    expect(addChildActionLabel("capability")).toBe("Add task or bug");
    expect(addChildActionLabel("milestone")).toBe("Add feature or bug");
  });

  it("resolves parent types for children", () => {
    // Initiatives hold features and bugs too, for sprint-style initiatives.
    expect(allowedParentTypes("feature")).toEqual(["initiative", "milestone"]);
    expect(allowedParentTypes("task")).toEqual(["capability"]);
    expect(allowedParentTypes("bug")).toEqual(["initiative", "milestone", "feature", "capability"]);
  });
});
