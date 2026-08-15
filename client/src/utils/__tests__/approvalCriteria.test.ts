import { impactWithoutCriteria } from "../approvalCriteria";

describe("impactWithoutCriteria", () => {
  it("drops the restated criteria section and keeps the brief", () => {
    const impact = [
      "Stage 'Playtest' requires human sign-off before completion.",
      "What's being tested: creature preview",
      "Acceptance criteria:",
      "- AC1: renders the scene",
      "- AC2: falls back to cage mode",
    ].join("\n");

    expect(impactWithoutCriteria(impact)).toBe(
      "Stage 'Playtest' requires human sign-off before completion.\nWhat's being tested: creature preview",
    );
  });

  it("matches the heading through markdown decoration", () => {
    expect(impactWithoutCriteria("Brief.\n## **Acceptance Criteria**\n- AC1: ok")).toBe("Brief.");
  });

  it("leaves an impact with no criteria section alone", () => {
    const impact = "Stage 'Playtest' requires human sign-off.\nThe acceptance criteria are met.";
    expect(impactWithoutCriteria(impact)).toBe(impact);
  });

  it("keeps a mention that is not its own heading", () => {
    const impact = "Acceptance criteria: see the ticket.\n- AC1: renders";
    expect(impactWithoutCriteria(impact)).toBe(impact);
  });
});
