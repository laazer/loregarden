import type { InferredGroup } from "../../api/memoryApi";
import { groupsFor, inferredEdges, recordTitle } from "../memoryInferred";

const group = (node_ids: string[], over: Partial<InferredGroup> = {}): InferredGroup => ({
  kind: "same_milestone",
  key: "m1",
  label: "Workflow Integrity",
  node_ids,
  ...over,
});

it("draws a group as a star around its first member, not a clique", () => {
  const edges = inferredEdges([group(["a", "b", "c", "d"])]);

  expect(edges.map((e) => [e.source_id, e.target_id])).toEqual([
    ["a", "b"],
    ["a", "c"],
    ["a", "d"],
  ]);
});

it("finds the groups one record is in", () => {
  const groups = [group(["a", "b"]), group(["b", "c"], { key: "t", kind: "shared_tag", label: "frontend" })];

  expect(groupsFor("a", groups).map((g) => g.label)).toEqual(["Workflow Integrity"]);
  expect(groupsFor("b", groups)).toHaveLength(2);
});

it("keeps a real title", () => {
  expect(recordTitle({ title: "A CLI that exits 0 part-way", excerpt: "## Signature ..." })).toBe(
    "A CLI that exits 0 part-way",
  );
});

it("replaces a ticket-id title with the body's bold lead", () => {
  expect(
    recordTitle({
      title: "Learning — lg-workflow-integrity-765",
      excerpt: "**A negated acceptance criterion routes the ticket to the thing it excludes.** More text.",
    }),
  ).toBe("A negated acceptance criterion routes the ticket to the thing it excludes.");
});

it("replaces a ticket-id title with the opening sentence, clipped at a word", () => {
  const title = recordTitle({
    title: "Learning — lg-bug-hole-574",
    excerpt:
      "Verifier (lg-bug-hole-574): implement claimed AC-12 coverage including legacy data migration, but no test imports it. Second.",
  });

  expect(title.endsWith("…")).toBe(true);
  expect(title.length).toBeLessThanOrEqual(91);
  expect(title.startsWith("Verifier (lg-bug-hole-574): implement claimed")).toBe(true);
});
