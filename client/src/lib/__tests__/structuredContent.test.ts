import {
  asDiffLines,
  asKeyValueRows,
  asRecordTable,
  firstProse,
  humanizeKey,
  isLongProse,
  isRedundantRow,
  splitRecord,
  unwrapSingleRecord,
} from "../structuredContent";

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

describe("recurring artifact shapes", () => {
  it("reads `{k, v}` rows, and nothing with any other field", () => {
    expect(asKeyValueRows([{ k: "status", v: "pass" }, { k: "confidence", v: "0.91" }])).toEqual([
      { k: "status", v: "pass" },
      { k: "confidence", v: "0.91" },
    ]);
    expect(asKeyValueRows([{ k: "status", v: "pass", note: "x" }])).toBeNull();
    expect(asKeyValueRows([{ k: "status", v: { nested: true } }])).toBeNull();
    expect(asKeyValueRows([])).toBeNull();
  });

  it("calls a row redundant when it is empty or repeats a fact", () => {
    const facts = new Set(["status"]);
    expect(isRedundantRow({ k: "status", v: "pass" }, facts)).toBe(true);
    expect(isRedundantRow({ k: "reroute_to_stage", v: "—" }, facts)).toBe(true);
    expect(isRedundantRow({ k: "Outcome", v: "passed" }, facts)).toBe(false);
  });

  it("tables uniform records, dropping a column empty in every row", () => {
    const table = asRecordTable([
      { item_key: "a", required: true, status: "complete", note: "" },
      { item_key: "b", required: false, status: "missing", note: "" },
    ]);
    expect(table?.columns).toEqual(["item_key", "required", "status"]);
    expect(table?.rows).toHaveLength(2);
  });

  it("does not table prose, nested values, a grab-bag, or a single record", () => {
    expect(asRecordTable([{ a: "x", b: "line\nbreak" }, { a: "y", b: "z" }])).toBeNull();
    expect(asRecordTable([{ a: "x", b: ["n"] }, { a: "y", b: ["m"] }])).toBeNull();
    expect(asRecordTable([{ a: 1, b: 2, c: 3 }, { d: 4, e: 5, f: 6 }])).toBeNull();
    expect(asRecordTable([{ a: 1, b: 2 }])).toBeNull();
  });

  it("reads a diff artifact's lines and nothing that merely has a type", () => {
    expect(asDiffLines([{ type: "h", ln: "", text: "@@ -1 +1 @@" }, { type: "a", ln: "", text: "x" }])).toEqual([
      { type: "h", text: "@@ -1 +1 @@" },
      { type: "a", text: "x" },
    ]);
    expect(asDiffLines([{ type: "finding", text: "x" }])).toBeNull();
  });

  it("unwraps a record whose only content is one nested record", () => {
    expect(unwrapSingleRecord({ handoff: { from_agent: "spec" } })).toEqual({ from_agent: "spec" });
    expect(unwrapSingleRecord({ handoff: { from_agent: "spec" }, empty: [] })).toEqual({ from_agent: "spec" });
    expect(unwrapSingleRecord({ handoff: { from_agent: "spec" }, note: "kept" })).toBeNull();
    expect(unwrapSingleRecord({ message: "a string" })).toBeNull();
  });
});
