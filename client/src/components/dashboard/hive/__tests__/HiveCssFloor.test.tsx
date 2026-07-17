import { render } from "@testing-library/react";

import { buildHiveWorld } from "../../../../lib/hive/worldModel";
import type { WorkflowStageView } from "../../../../api/client";
import { HiveCssFloor } from "../HiveCssFloor";

const stage = (
  overrides: Partial<WorkflowStageView> & Pick<WorkflowStageView, "key" | "name" | "agent_id">,
): WorkflowStageView => ({
  status: "running",
  skill_name: "apply_patch",
  optional: false,
  note: "",
  stage_type: "agent",
  agents: [],
  ...overrides,
});

const officeplaceModel = () =>
  buildHiveWorld([stage({ key: "impl", name: "Implementation", agent_id: "backend_implementer" })], {
    skin: "officeplace",
  });

describe("HiveCssFloor — officeplace background", () => {
  it("renders the background image as the floor", () => {
    const { container } = render(<HiveCssFloor model={officeplaceModel()} />);
    const floor = container.querySelector(".hive-css");
    expect(floor?.className).toContain("hive-css--scenery");
  });

  it("suppresses the drawn floor plan when a background image is present", () => {
    const { container } = render(<HiveCssFloor model={officeplaceModel()} />);
    // The image IS the floor — no drawn rooms, desks, props or doors on top of it.
    expect(container.querySelectorAll(".hive-css__room")).toHaveLength(0);
    expect(container.querySelectorAll(".hive-css__prop")).toHaveLength(0);
    expect(container.querySelector('[data-testid^="hive-door"]')).toBeNull();
  });

  it("still renders the NPC data layer on top of the background", () => {
    const { container, queryByTestId } = render(<HiveCssFloor model={officeplaceModel()} />);
    // Agents, stations and the receptionist are the "thin data layer" over the image.
    expect(container.querySelectorAll(".hive-css__agent").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".hive-css__station").length).toBeGreaterThan(0);
    expect(queryByTestId("hive-receptionist")).not.toBeNull();
  });
});
