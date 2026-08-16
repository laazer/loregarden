import { checklistCoversCriteria, impactWithoutCriteria } from "../approvalCriteria";

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

describe("checklistCoversCriteria", () => {
  const criteria = ["AC1: renders the scene", "AC2: falls back to cage mode"];

  it("is true when every criterion has an expanded item", () => {
    expect(
      checklistCoversCriteria(
        [
          "Play-test by hand — AC1: renders the scene",
          "Play-test by hand — AC2: falls back to cage mode",
          "Confirm no console errors",
        ],
        criteria,
      ),
    ).toBe(true);
  });

  it("is false when a criterion has no item", () => {
    expect(
      checklistCoversCriteria(["Play-test by hand — AC1: renders the scene"], criteria),
    ).toBe(false);
  });

  it("is false for an unrelated checklist", () => {
    expect(checklistCoversCriteria(["Check the logs"], criteria)).toBe(false);
  });

  it("is false when either side is empty", () => {
    expect(checklistCoversCriteria([], criteria)).toBe(false);
    expect(checklistCoversCriteria(["Play-test by hand — AC1: renders the scene"], [])).toBe(false);
  });
});
