import type { LedgerAttempt, LedgerVisit, TicketArtifactItem } from "../../api/types";
import { buildTicketTimeline, placeOutputs, reportVerdict } from "../ticketTimeline";

function attempt(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "succeeded",
    started_at: "2026-09-20T10:00:00+00:00",
    finished_at: "2026-09-20T10:10:00+00:00",
    duration_seconds: 600,
    ...overrides,
  };
}

function visit(stage_key: string, attempts: LedgerAttempt[], overrides: Partial<LedgerVisit> = {}): LedgerVisit {
  return { stage_key, visit_number: 1, status: "succeeded", is_parallel: false, attempts, ...overrides };
}

let nextId = 0;
function item(overrides: Partial<TicketArtifactItem> = {}): TicketArtifactItem {
  nextId += 1;
  return {
    id: `a${nextId}`,
    kind: "handoff",
    title: "",
    run_id: null,
    stage_key: null,
    system: false,
    evidence_kind: "",
    commit_sha: "",
    created_at: "2026-09-20T10:05:00",
    content_bytes: 2,
    content: {},
    ...overrides,
  };
}

// implement (10:00–10:10) → verify (10:20–10:30) → implement again (10:40–10:50)
const implement1 = visit("implement", [attempt({ run_id: "imp1" })]);
const verify = visit("verify", [
  attempt({ run_id: "ver1", started_at: "2026-09-20T10:20:00+00:00", finished_at: "2026-09-20T10:30:00+00:00" }),
]);
const implement2 = visit(
  "implement",
  [attempt({ run_id: "imp2", started_at: "2026-09-20T10:40:00+00:00", finished_at: "2026-09-20T10:50:00+00:00" })],
  { visit_number: 2 },
);
const visits = [implement1, verify, implement2];

describe("placeOutputs", () => {
  it("puts a row that records its run on that run's visit, whatever its time or stage say", () => {
    const row = item({ run_id: "imp1", stage_key: "verify", created_at: "2026-09-20T10:45:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(0);
  });

  it("puts a row naming a stage on the latest visit of that stage begun by then", () => {
    // Landed during verify, but says it is about implement: the first implement
    // visit, not verify and not the implement visit that had not started yet.
    const row = item({ stage_key: "implement", created_at: "2026-09-20T10:25:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(0);

    const later = item({ stage_key: "implement", created_at: "2026-09-20T10:55:00" });
    expect(placeOutputs(visits, [later]).get(later.id)).toBe(2);
  });

  it("puts an untagged row on the visit under way when it landed", () => {
    const row = item({ created_at: "2026-09-20T10:35:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(1);
  });

  it("falls back to time when the stage it names never ran", () => {
    const row = item({ stage_key: "gate", created_at: "2026-09-20T10:45:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(2);
  });

  it("reads an offset-less server timestamp as UTC, as the ledger's are", () => {
    // 10:15 UTC is between implement and verify. Read as local time it would
    // shift by the viewer's offset and land somewhere else entirely.
    const row = item({ created_at: "2026-09-20T10:15:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(0);
  });

  it("keeps a row from before any run apart", () => {
    const row = item({ created_at: "2026-09-20T09:00:00" });
    expect(placeOutputs(visits, [row]).get(row.id)).toBe(-1);
  });
});

describe("buildTicketTimeline", () => {
  it("leaves system records out of the story", () => {
    const timeline = buildTicketTimeline(visits, [
      item({ kind: "stage_dispatch", system: true }),
      item({ title: "kept" }),
    ]);
    expect(timeline.visits[0].outputs.map((output) => output.title)).toEqual(["kept"]);
  });

  it("lists a visit's outputs oldest first", () => {
    const timeline = buildTicketTimeline(visits, [
      item({ title: "second", created_at: "2026-09-20T10:08:00" }),
      item({ title: "first", created_at: "2026-09-20T10:02:00" }),
    ]);
    expect(timeline.visits[0].outputs.map((output) => output.title)).toEqual(["first", "second"]);
  });

  it("counts failed runs and error outputs separately", () => {
    const timeline = buildTicketTimeline(
      [
        visit("gate", [
          attempt({ run_id: "g1", status: "failed" }),
          attempt({ run_id: "g2", status: "cancelled" }),
          attempt({ run_id: "g3" }),
        ]),
      ],
      [item({ kind: "error" })],
    );
    expect(timeline.visits[0].failedAttempts.map((a) => a.run_id)).toEqual(["g1"]);
    expect(timeline.visits[0].errorCount).toBe(1);
  });

  it("times a fan-out by its wall clock, not the sum of its lanes", () => {
    const lanes = visit(
      "review",
      [
        attempt({ run_id: "l1", agent_id: "static_qa" }),
        attempt({ run_id: "l2", agent_id: "security_reviewer", finished_at: "2026-09-20T10:12:00+00:00" }),
      ],
      { is_parallel: true },
    );
    expect(buildTicketTimeline([lanes], []).visits[0].durationSeconds).toBe(720);
  });

  it("has no duration and is active while an attempt runs", () => {
    const running = visit("implement", [attempt({ status: "running", finished_at: null })], { status: "running" });
    const [only] = buildTicketTimeline([running], []).visits;
    expect(only.active).toBe(true);
    expect(only.durationSeconds).toBeNull();
  });
});

describe("reportVerdict", () => {
  it("reads a stage report's status and confidence", () => {
    expect(reportVerdict({ stage_key: "gate", status: "pass", confidence: 0.91 })).toEqual({
      status: "pass",
      confidence: 0.91,
    });
  });

  it("is null for anything that is not a stage report", () => {
    expect(reportVerdict({ status: "pass" })).toBeNull();
    expect(reportVerdict([1, 2])).toBeNull();
    expect(reportVerdict(null)).toBeNull();
  });
});
