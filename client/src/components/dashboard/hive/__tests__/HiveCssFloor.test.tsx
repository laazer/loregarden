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

describe("HiveCssFloor — crewed stations", () => {
  const mdrModel = () =>
    buildHiveWorld([stage({ key: "test", name: "Testing", agent_id: "static_qa" })], {
      skin: "officeplace",
    });

  it("puts the whole MDR crew on the floor for one testing agent", () => {
    const { container } = render(<HiveCssFloor model={mdrModel()} />);
    expect(container.querySelectorAll(".hive-css__agent")).toHaveLength(4);
  });

  it("gives every crew member a sprite", () => {
    const { container } = render(<HiveCssFloor model={mdrModel()} />);
    const srcs = [...container.querySelectorAll<HTMLImageElement>(".hive-css__agent-avatar")].map(
      (img) => img.getAttribute("src"),
    );
    // Distinctness is asserted against the manifest instead: jest maps every
    // .png to one fileMock, so all four srcs are the same string here.
    expect(srcs).toHaveLength(4);
    expect(srcs.every(Boolean)).toBe(true);
  });

  it("shows a single status card for the crew, not one per body", () => {
    const { container } = render(<HiveCssFloor model={mdrModel()} />);
    // Four colleagues, one agent — so one card, or the floor reads as four agents.
    expect(container.querySelectorAll(".hive-css__agent-card")).toHaveLength(1);
  });
});
