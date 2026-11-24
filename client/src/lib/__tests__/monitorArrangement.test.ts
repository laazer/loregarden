import { arrangeFindings, filterArrangement, isEmptyArrangement } from "../monitorArrangement";
import type { MonitorFinding } from "../../api/types";

const finding = (over: Partial<MonitorFinding> = {}): MonitorFinding => ({
  condition: "stage_thrash",
  ticket_id: "t1",
  stage_key: "implement",
  summary: "ran 12 times",
  evidence: {},
  occurrences: 1,
  first_seen: null,
  last_seen: null,
  ticket_title: "",
  ticket_external_id: "",
  ticket_state: "in_progress",
  workspace_slug: "",
  ...over,
});

const on = (ticket: string, over: Partial<MonitorFinding> = {}) =>
  finding({ ticket_id: ticket, ticket_external_id: `lg-${ticket}`, ...over });

const ids = (tickets: { ticketId: string }[]) => tickets.map((ticket) => ticket.ticketId).sort();

/** Two tickets over a cap (one also stalled), one lone thrash, one workspace finding. */
const FINDINGS = [
  on("a", { condition: "invariant_unrepaired", stage_key: "caps", summary: "split it", evidence: { detail: "a holds 18" } }),
  on("b", { condition: "invariant_unrepaired", stage_key: "caps", summary: "split it", evidence: { detail: "b holds 13" } }),
  on("b", { condition: "stalled_run", stage_key: "plan", summary: "running for 4h" }),
  on("c", { ticket_title: "Alone", workspace_slug: "blobert" }),
  finding({ ticket_id: "", condition: "draft_drift", stage_key: "", summary: "draft differs", workspace_slug: "loregarden" }),
  finding({ ticket_id: "", condition: "draft_drift", stage_key: "plan", summary: "other draft", workspace_slug: "blobert" }),
];

it("lists a recurring finding once: in its group, and not again by ticket", () => {
  const view = arrangeFindings(FINDINGS, null);

  expect(view.recurring).toHaveLength(1);
  expect(ids(view.recurring[0].tickets.map(({ ticket }) => ticket))).toEqual(["a", "b"]);
  expect(ids(view.others)).toEqual(["b", "c"]);
  expect(view.others.find(({ ticketId }) => ticketId === "b")?.lines.map(({ condition }) => condition)).toEqual([
    "stalled_run",
  ]);
  expect(view.workspace.map(({ findings }) => findings.length)).toEqual([2]);
});

it("accounts for every ticket with a finding, in one list or the other", () => {
  const view = arrangeFindings(FINDINGS, null);
  const named = [...view.recurring.flatMap(({ tickets }) => tickets.map(({ ticket }) => ticket)), ...view.others];

  expect(new Set(ids(named))).toEqual(new Set(["a", "b", "c"]));
});

it("keeps each ticket's own detail beside a sentence they share", () => {
  const [row] = arrangeFindings(FINDINGS, null).recurring;

  expect(row.sharedSummary).toBe("split it");
  expect(row.tickets.map(({ line }) => line.detail).sort()).toEqual(["a holds 18", "b holds 13"]);
});

it("shares no sentence when the tickets say different things", () => {
  const [row] = arrangeFindings([on("a", { summary: "ran 29 times" }), on("b", { summary: "ran 4 times" })], null)
    .recurring;

  expect(row.sharedSummary).toBeNull();
  expect(row.tickets.map(({ line }) => line.summary).sort()).toEqual(["ran 29 times", "ran 4 times"]);
});

it("keeps every line on the selected ticket, recurring or not, and off the list below", () => {
  const view = arrangeFindings(FINDINGS, "b");

  expect(view.current?.lines).toHaveLength(2);
  expect(ids(view.others)).toEqual(["c"]);
});

it.each([
  ["a blank query keeps everything", "  ", { recurring: ["a", "b"], others: ["b", "c"], workspace: 2 }],
  ["a ticket keeps its row in the group, still a group", "lg-a", { recurring: ["a"], others: [], workspace: 0 }],
  ["case does not matter", "LG-A", { recurring: ["a"], others: [], workspace: 0 }],
  ["a title", "alone", { recurring: [], others: ["c"], workspace: 0 }],
  ["a workspace", "blobert", { recurring: [], others: ["c"], workspace: 1 }],
  ["a condition's label keeps the whole group", "invariant unrep", { recurring: ["a", "b"], others: [], workspace: 0 }],
  ["a group's stage keeps the whole group", "caps", { recurring: ["a", "b"], others: [], workspace: 0 }],
  ["a shared sentence keeps the whole group", "split", { recurring: ["a", "b"], others: [], workspace: 0 }],
  ["one ticket's detail", "holds 18", { recurring: ["a"], others: [], workspace: 0 }],
  ["a line's stage", "plan", { recurring: [], others: ["b"], workspace: 1 }],
  ["a line's sentence", "4h", { recurring: [], others: ["b"], workspace: 0 }],
  ["a workspace condition keeps its row whole", "draft drift", { recurring: [], others: [], workspace: 2 }],
  ["a workspace finding's sentence", "other draft", { recurring: [], others: [], workspace: 1 }],
])("filters by %s", (_name, query, expected) => {
  const view = filterArrangement(arrangeFindings(FINDINGS, null), query);

  expect({
    recurring: ids(view.recurring.flatMap(({ tickets }) => tickets.map(({ ticket }) => ticket))),
    others: ids(view.others),
    workspace: view.workspace.flatMap(({ findings }) => findings).length,
  }).toEqual(expected);
  expect(view.recurring.every(({ group }) => group.tickets === 2)).toBe(true);
});

it("narrows a matched line's ticket to the lines that match", () => {
  const view = filterArrangement(arrangeFindings(FINDINGS, "b"), "4h");

  expect(view.current?.lines.map(({ condition }) => condition)).toEqual(["stalled_run"]);
  expect(filterArrangement(arrangeFindings(FINDINGS, "b"), "lg-a").current).toBeNull();
});

it("is empty only when every section is", () => {
  expect(isEmptyArrangement(arrangeFindings([], null))).toBe(true);
  for (const only of [[FINDINGS[3]], [FINDINGS[4]], FINDINGS.slice(0, 2)]) {
    expect(isEmptyArrangement(arrangeFindings(only, null))).toBe(false);
  }
  expect(isEmptyArrangement(arrangeFindings([FINDINGS[3]], "c"))).toBe(false);
  expect(isEmptyArrangement(filterArrangement(arrangeFindings(FINDINGS, null), "zzz"))).toBe(true);
});
