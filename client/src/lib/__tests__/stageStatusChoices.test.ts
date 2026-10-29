import { stageStatusChoices } from "../stageStatusChoices";

describe("stageStatusChoices", () => {
  it("does not offer awaiting for a stage that is not already awaiting", () => {
    expect(stageStatusChoices("pending")).not.toContain("awaiting");
    expect(stageStatusChoices(null)).not.toContain("awaiting");
  });

  it("keeps awaiting for a stage that already awaits its approval", () => {
    expect(stageStatusChoices("awaiting")).toContain("awaiting");
  });
});
