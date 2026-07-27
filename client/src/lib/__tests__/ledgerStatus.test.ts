import { ACTIVE_LEDGER_STATUSES } from "../ledgerStatus";

// Requirement 1 — the single source of truth for "is this attempt still
// running" that RunLedgerPanel and LogsPanel's lane derivation both import,
// rather than each typing out its own literal set that can drift.

it("treats running, queued, and awaiting_permission as active", () => {
  expect(ACTIVE_LEDGER_STATUSES.has("running")).toBe(true);
  expect(ACTIVE_LEDGER_STATUSES.has("queued")).toBe(true);
  expect(ACTIVE_LEDGER_STATUSES.has("awaiting_permission")).toBe(true);
});

it("does not treat terminal statuses as active", () => {
  expect(ACTIVE_LEDGER_STATUSES.has("completed")).toBe(false);
  expect(ACTIVE_LEDGER_STATUSES.has("failed")).toBe(false);
  expect(ACTIVE_LEDGER_STATUSES.has("succeeded")).toBe(false);
});

it("is case-sensitive — ledger statuses arrive already lowercase, and this set must not silently swallow a mismatch", () => {
  expect(ACTIVE_LEDGER_STATUSES.has("RUNNING")).toBe(false);
  expect(ACTIVE_LEDGER_STATUSES.has("Running")).toBe(false);
});

it("does not treat empty string or whitespace-padded statuses as active", () => {
  expect(ACTIVE_LEDGER_STATUSES.has("")).toBe(false);
  expect(ACTIVE_LEDGER_STATUSES.has(" running")).toBe(false);
  expect(ACTIVE_LEDGER_STATUSES.has("running ")).toBe(false);
});

it("contains exactly the three documented statuses — no extra members that would silently widen what counts as a running lane", () => {
  expect(ACTIVE_LEDGER_STATUSES.size).toBe(3);
  expect([...ACTIVE_LEDGER_STATUSES].sort()).toEqual(["awaiting_permission", "queued", "running"]);
});
