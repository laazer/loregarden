import { impactWithoutCriteria, shortenRestatedChecklist } from "../approvalCriteria";

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

describe("shortenRestatedChecklist", () => {
  const criteria = [
    "AC1: CreaturePreview exposes a frontend-local API",
    "AC2: Live updates are debounced by 100 ms or less",
  ];

  it("replaces a restated criterion with the id it points at", () => {
    expect(
      shortenRestatedChecklist(
        [
          "Play-test by hand — AC1: CreaturePreview exposes a frontend-local API",
          "Play-test by hand — AC2: Live updates are debounced by 100 ms or less",
        ],
        criteria,
      ),
    ).toEqual(["Play-test by hand — AC1", "Play-test by hand — AC2"]);
  });

  it("leaves hand-written checks alone", () => {
    const items = ["Confirm no console errors appear during play"];
    expect(shortenRestatedChecklist(items, criteria)).toEqual(items);
  });

  it("leaves an item alone when the criterion has no id to point at", () => {
    const items = ["Play-test by hand — Dash has a cooldown"];
    expect(shortenRestatedChecklist(items, ["Dash has a cooldown"])).toEqual(items);
  });

  it("keeps an item that is exactly the criterion, with no prefix to keep", () => {
    const items = ["AC1: CreaturePreview exposes a frontend-local API"];
    expect(shortenRestatedChecklist(items, criteria)).toEqual(items);
  });

  it("passes everything through when the ticket records no criteria", () => {
    const items = ["Play-test by hand — AC1: CreaturePreview exposes a frontend-local API"];
    expect(shortenRestatedChecklist(items, [])).toEqual(items);
  });
});
