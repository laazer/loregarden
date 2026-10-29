import type { TicketDetail } from "../../api/client";
import type { LedgerAttempt, LedgerVisit } from "../../api/types";
import { attemptRows, isErrorLine, ticketFailure } from "../ticketFailure";
import { buildTicketTimeline } from "../ticketTimeline";

function attempt(run_id: string, status = "failed", error_tail = ""): LedgerAttempt {
  return {
    run_id,
    run_code: `run_${run_id}`,
    agent_id: "implementer",
    skill_name: "",
    status,
    started_at: "2026-10-24T11:35:00+00:00",
    finished_at: "2026-10-24T11:36:00+00:00",
    duration_seconds: 60,
    error_tail,
  };
}

function visits(...raw: [string, LedgerAttempt[]][]) {
  const ledger: LedgerVisit[] = raw.map(([stage_key, attempts]) => ({
    stage_key,
    visit_number: 1,
    status: attempts[attempts.length - 1].status,
    is_parallel: false,
    attempts,
  }));
  return buildTicketTimeline(ledger, []).visits;
}

const blocked = { state: "blocked", block_message: "stopped", blocking_issues: "", artifacts: {} } as unknown as TicketDetail;

describe("ticketFailure", () => {
  it("is null for a ticket nothing has stopped", () => {
    const fine = { state: "in_progress", blocking_issues: "", artifacts: {} } as unknown as TicketDetail;
    expect(ticketFailure(fine, visits(["plan", [attempt("p1", "failed")]]))).toBeNull();
  });

  it("finds the newest failed attempt of the newest stage that has not recovered", () => {
    const found = ticketFailure(
      blocked,
      visits(["plan", [attempt("p1")]], ["implement", [attempt("i1"), attempt("i2"), attempt("i3", "cancelled")]]),
    );
    expect(found?.visit?.visit.stage_key).toBe("implement");
    expect(found?.attempt?.run_id).toBe("i2");
    expect(found?.attemptNumber).toBe(2);
  });

  it("prefers the error's own run, wherever it sits", () => {
    const withError = {
      ...blocked,
      artifacts: { error: { message: "x", run_code: "run_p1", agent_id: "", stage_key: "plan", command: "" } },
    } as unknown as TicketDetail;
    const found = ticketFailure(withError, visits(["plan", [attempt("p1")]], ["implement", [attempt("i1")]]));
    expect(found?.attempt?.run_id).toBe("p1");
  });

  it("names no attempt when every stage recovered", () => {
    const found = ticketFailure(blocked, visits(["implement", [attempt("i1"), attempt("i2", "succeeded")]]));
    expect(found?.attempt).toBeNull();
    expect(found?.message).toBe("stopped");
  });
});

describe("attemptRows", () => {
  it("folds two or more failures in a row, and says when they failed the same way", () => {
    const rows = attemptRows(
      [attempt("a", "failed", "E"), attempt("b", "failed", "E"), attempt("c", "succeeded"), attempt("d", "failed", "E")],
      { isParallel: false, standAlone: null },
    );
    expect(rows.map((row) => row.kind)).toEqual(["failed-run", "attempt", "attempt"]);
    expect(rows[0]).toMatchObject({ first: 1, last: 2, sameError: true });
  });

  it("never folds the attempt shown above, nor claims a shared error nobody recorded", () => {
    const rows = attemptRows([attempt("a"), attempt("b"), attempt("c")], { isParallel: false, standAlone: "c" });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ kind: "failed-run", first: 1, last: 2, sameError: false });
    expect(rows[1]).toMatchObject({ kind: "attempt", number: 3 });
  });

  it("leaves a fan-out's lanes apart", () => {
    const rows = attemptRows([attempt("a"), attempt("b")], { isParallel: true, standAlone: null });
    expect(rows.map((row) => row.kind)).toEqual(["attempt", "attempt"]);
  });
});

it("marks the lines that state an error, not the narration around them", () => {
  expect(isErrorLine("Agent launch failed: command exceeded the system limit")).toBe(true);
  expect(isErrorLine("tmux: command too long")).toBe(true);
  expect(isErrorLine("AssertionError: boom")).toBe(true);
  expect(isErrorLine("Run run_d4 failed")).toBe(false);
});
