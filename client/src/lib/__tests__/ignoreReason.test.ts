import type { Approval } from "../../api/client";
import { duplicateApprovalIds, suggestIgnoreReason } from "../ignoreReason";

const NOW = new Date("2026-10-12T12:00:00Z");

function card(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "a",
    title: "Approve Review",
    level: "medium",
    workspace_slug: "w",
    stage_key: "review",
    stage_name: "Review",
    impact: "",
    ticket_id: "t1",
    ticket_external_id: "lg-1",
    kind: "workflow_gate",
    run_id: "",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "",
    created_at: "2026-10-12T10:00:00Z",
    ticket_state: "in_progress",
    ...overrides,
  };
}

describe("suggestIgnoreReason", () => {
  it("leads with a finished ticket over everything else", () => {
    expect(
      suggestIgnoreReason(card({ ticket_state: "wont_do" }), { hasDuplicate: true, now: NOW }),
    ).toMatch(/^Ticket completed: lg-1 is closed as won't do/);
  });

  it("names a duplicate next", () => {
    expect(suggestIgnoreReason(card(), { hasDuplicate: true, now: NOW })).toMatch(/^Duplicate:/);
  });

  it("calls an old card out of date, with its age", () => {
    expect(
      suggestIgnoreReason(card({ created_at: "2026-10-08T12:00:00Z" }), {
        hasDuplicate: false,
        now: NOW,
      }),
    ).toMatch(/^Out of date: .* 4 days/);
  });

  it("never returns an empty reason", () => {
    expect(suggestIgnoreReason(card({ created_at: "" }), { hasDuplicate: false, now: NOW })).toMatch(
      /^Superseded: /,
    );
  });
});

describe("duplicateApprovalIds", () => {
  it("pairs cards sharing a ticket and title, and nothing else", () => {
    const ids = duplicateApprovalIds([
      card({ id: "a" }),
      card({ id: "b" }),
      card({ id: "c", ticket_id: "t2" }),
      card({ id: "d", title: "Other" }),
    ]);
    expect([...ids].sort()).toEqual(["a", "b"]);
  });
});
