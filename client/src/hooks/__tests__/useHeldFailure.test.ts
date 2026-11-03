/**
 * lg-milestone-that-1435 AC2/AC3 — the held-failure contract.
 *
 * Two surfaces render an error pane whose only control re-runs the query the
 * pane is reporting on. query-core clears `error` and puts a failed-with-no-data
 * query back to `status: "pending"` the moment `refetch()` is called (5.101.2
 * `query.ts:719-728`), so a branch guarded on `isError` alone unmounts on the
 * operator's own Retry click and takes the reason with it. This hook is the
 * hold that outlives that transition.
 *
 * It is tested here rather than only through its callers because the keying
 * invariant — a failure must never be returned beside a different subject — is
 * *unobservable* from `WorkflowMonitorView`, whose query key is the constant
 * `["monitor-findings", "all"]`. The subject is a parameter here, so varying it
 * is the only place the invariant can go red.
 *
 * The three query states below are query-core's, not inventions:
 *   failed, idle        → { isError: true,  error: E,    isFetching: false }
 *   failed, refetching  → { isError: false, error: null, isFetching: true  }   ← the defect
 *   settled, no failure → { isError: false, error: null, isFetching: false }
 *   failed, still fetching → { isError: true,  error: E,    isFetching: true  }
 *
 * The fourth is the one a reading of the contract can lose: query-core keeps
 * `error` and `status: "error"` when `data` is defined, so a failed poll on a
 * query that has loaded once arrives with a fetch already in flight. Step 1 of
 * the contract is unconditional on `isFetching` for that reason.
 */

import { renderHook } from "@testing-library/react";

import { useHeldFailure } from "../useHeldFailure";

const FAILED = { isError: true, isFetching: false };
const FAILED_WHILE_FETCHING = { isError: true, isFetching: true };
const REFETCHING = { isError: false, error: null, isFetching: true };
const SETTLED = { isError: false, error: null, isFetching: false };

type Query = { isError: boolean; error: unknown; isFetching: boolean };

function hold<TSubject>(query: Query, subject: TSubject) {
  return renderHook(
    ({ query: q, subject: s }: { query: Query; subject: TSubject }) => useHeldFailure(q, s),
    { initialProps: { query, subject } },
  );
}

describe("useHeldFailure", () => {
  it("returns the failure the query is reporting, with its subject", () => {
    const error = new Error("the endpoint did not answer");

    const { result } = hold({ ...FAILED, error }, "all");

    expect(result.current).toEqual({ subject: "all", error });
  });

  it("returns null while a read that has not failed is in flight", () => {
    const { result } = hold(REFETCHING, "all");

    expect(result.current).toBeNull();
  });

  it("keeps the failure across the query's own refetch, which clears error", () => {
    // The whole point of the hook. `refetch()` on a failed-with-no-data query
    // leaves isError false and isFetching true, so neither the store nor the
    // clear runs and the previous failure is still what the caller renders.
    const error = new Error("the endpoint did not answer");
    const { result, rerender } = hold({ ...FAILED, error }, "all");

    rerender({ query: REFETCHING, subject: "all" });

    expect(result.current).toEqual({ subject: "all", error });
  });

  it("clears the failure once a read settles without failing", () => {
    const { result, rerender } = hold({ ...FAILED, error: new Error("boom") }, "all");

    rerender({ query: SETTLED, subject: "all" });

    expect(result.current).toBeNull();
  });

  it("does not resurrect a cleared failure when the next read starts", () => {
    // The clear condition is `!isFetching`, so a later in-flight render must
    // find an empty store rather than the failure two reads ago.
    const { result, rerender } = hold({ ...FAILED, error: new Error("boom") }, "all");
    rerender({ query: SETTLED, subject: "all" });

    rerender({ query: REFETCHING, subject: "all" });

    expect(result.current).toBeNull();
  });

  it("replaces the held failure when the query fails again with a different error", () => {
    const first = new Error("first reason");
    const second = new Error("second reason");
    const { result, rerender } = hold({ ...FAILED, error: first }, "all");

    rerender({ query: REFETCHING, subject: "all" });
    rerender({ query: { ...FAILED, error: second }, subject: "all" });

    expect(result.current).toEqual({ subject: "all", error: second });
  });

  it("holds a null subject, which is what a caller with no selection passes", () => {
    // GateStudioPanel's subject is `target.workspaceSlug`, which is null before
    // a workspace is chosen. Object.is(null, null) is true, so the hold matches.
    const error = new Error("boom");
    const { result, rerender } = hold<string | null>({ ...FAILED, error }, null);

    rerender({ query: REFETCHING, subject: null });

    expect(result.current).toEqual({ subject: null, error });
  });

  it("stores a failure that arrives with a fetch already in flight", () => {
    // Step 1 is unconditional on `isFetching`. query-core only clears `error`
    // when `data === undefined`, so a query that has loaded once reports a
    // failed poll as isError true WITH isFetching true. A hook that reads
    // `isFetching` first and treats an in-flight render as "hold whatever you
    // had" never stores this failure at all — and passes every other test on
    // this page, because the three first-load shapes never produce it.
    const error = new Error("the poll failed");

    const { result } = hold({ ...FAILED_WHILE_FETCHING, error }, "all");

    expect(result.current).toEqual({ subject: "all", error });
  });

  it("holds a failure that carries no error value", () => {
    // `error` is whatever was thrown, and nothing guarantees it is truthy.
    // The entry is the answer to "did this read fail", so it must be returned
    // on its own existence, not on its payload being inspectable — a caller
    // deciding `if (failure)` would otherwise fall through to a branch that
    // reports health nobody asked about. describeError's fallback is what
    // supplies the words.
    const { result } = hold({ ...FAILED, error: null }, "all");

    expect(result.current).toEqual({ subject: "all", error: null });
  });

  it("returns the same held failure on every render, not just the first read after it", () => {
    // A store that is emptied by the act of being read would satisfy every
    // single-rerender assertion here and then blank the pane on the next
    // render React happens to do — a parent re-render, a sibling's state
    // change, the 30s poll tick.
    const error = new Error("the endpoint did not answer");
    const { result, rerender } = hold({ ...FAILED, error }, "all");

    rerender({ query: REFETCHING, subject: "all" });
    rerender({ query: REFETCHING, subject: "all" });

    expect(result.current).toEqual({ subject: "all", error });
  });

  describe("the keying invariant", () => {
    it("returns null as soon as the subject changes, even while the new read is in flight", () => {
      // The shape lg-milestone-that-1434's architecture review found broken in
      // GateStudioPanel: switching workspaces starts a brand-new observer
      // (isError false, isFetching true), so the clear is skipped and an unkeyed
      // ref renders the previous workspace's failure under the new name.
      const error = new Error("workspace a could not be read");
      const { result, rerender } = hold({ ...FAILED, error }, "a");

      rerender({ query: REFETCHING, subject: "b" });

      expect(result.current).toBeNull();
    });

    it("returns null for a different subject while that subject's query is idle", () => {
      const { result, rerender } = hold({ ...FAILED, error: new Error("a failed") }, "a");

      rerender({ query: SETTLED, subject: "b" });

      expect(result.current).toBeNull();
    });

    it("reports the new subject's own failure, not the one held for the old subject", () => {
      const forA = new Error("a failed");
      const forB = new Error("b failed");
      const { result, rerender } = hold({ ...FAILED, error: forA }, "a");

      rerender({ query: REFETCHING, subject: "b" });
      rerender({ query: { ...FAILED, error: forB }, subject: "b" });

      expect(result.current).toEqual({ subject: "b", error: forB });
    });

    it("withholds rather than discards: a mismatch leaves the hold intact", () => {
      // Step 3 of the contract is a comparison, not a second clear. A failure
      // for "a" is still "a"'s failure while another subject's read is in
      // flight, so coming back to "a" before anything settled must still show
      // it — the alternative drops a true failure on a round trip and lands the
      // caller on a branch that reports health nobody asked about.
      const error = new Error("a failed");
      const { result, rerender } = hold({ ...FAILED, error }, "a");

      rerender({ query: REFETCHING, subject: "b" });
      rerender({ query: REFETCHING, subject: "a" });

      expect(result.current).toEqual({ subject: "a", error });
    });


    it("clears for every subject once any read settles without failing", () => {
      // Step 2 is not keyed: a settled, failure-free read is evidence the hold
      // is stale, whoever it was held for. Keying the *clear* to the subject
      // would leave a workspace's failure alive across a switch away and back
      // with nothing able to retire it — the unkeyed hold of
      // lg-milestone-that-1434 wearing a comparison.
      const { result, rerender } = hold({ ...FAILED, error: new Error("a failed") }, "a");

      rerender({ query: SETTLED, subject: "b" });
      rerender({ query: REFETCHING, subject: "a" });

      expect(result.current).toBeNull();
    });

    it("keys on a falsy subject by value, not by truthiness", () => {
      // The empty string is a subject like any other. A comparison written as
      // `!subject || Object.is(...)` reads as a convenience and is a hole: it
      // returns one subject's failure for every other subject.
      const error = new Error("the unnamed subject failed");
      const { result, rerender } = hold<string>({ ...FAILED, error }, "");

      rerender({ query: REFETCHING, subject: "a" });
      expect(result.current).toBeNull();

      rerender({ query: REFETCHING, subject: "" });
      expect(result.current).toEqual({ subject: "", error });
    });

    it("compares subjects with Object.is", () => {
      // AC1 names Object.is, and NaN is the only input that separates it from
      // `===`: Object.is(NaN, NaN) is true where NaN === NaN is false. Pinned
      // because a `===` comparison passes every other test on this page while
      // silently withholding a failure whose subject never changed.
      const error = new Error("boom");
      const { result, rerender } = hold({ ...FAILED, error }, Number.NaN);

      rerender({ query: REFETCHING, subject: Number.NaN });

      expect(result.current).toEqual({ subject: Number.NaN, error });
    });
  });
});
