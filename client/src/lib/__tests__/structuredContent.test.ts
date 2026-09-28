import { firstProse, humanizeKey, isLongProse, splitRecord } from "../structuredContent";

it("puts short scalars in facts and prose under sections, body fields first", () => {
  const { facts, sections } = splitRecord({
    verdict: "pass",
    task_count: 3,
    findings: ["one", "two"],
    summary: "Line one\nline two",
    open_questions: [],
  });

  expect(facts).toEqual([
    ["verdict", "pass"],
    ["task_count", 3],
  ]);
  expect(sections.map(([key]) => key)).toEqual(["summary", "findings"]);
});

it("treats a long or multi-line string as prose, not a fact", () => {
  const { facts } = splitRecord({ note: "x".repeat(81), detail: "a\nb" });

  expect(facts).toEqual([]);
});

it("humanizes snake_case keys", () => {
  expect(humanizeKey("acceptance_criteria_status")).toBe("Acceptance criteria status");
});

it("finds the first readable prose, preferring body fields", () => {
  expect(firstProse({ status: "ok", markdown: "# Title\n\nBody" })).toBe("# Title\n\nBody");
  expect(firstProse({ steps: [{ detail: "do it" }] })).toBe("do it");
});

it("calls prose long by length or line count", () => {
  expect(isLongProse("short")).toBe(false);
  expect(isLongProse("x".repeat(601))).toBe(true);
  expect(isLongProse(Array(14).fill("line").join("\n"))).toBe(true);
});
