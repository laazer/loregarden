/**
 * lg-milestone-that-2187 R0-1..R0-4 — the one predicate the five polling
 * surfaces share.
 *
 * `useHeldFailure` answers "did the last read fail, for this subject". It does
 * not answer the question the four defective surfaces got wrong, which is
 * *which pane that failure justifies*. query-core keeps `data` and sets
 * `status: "error"` when a refetch fails over data already present, so a branch
 * guarded on the failure alone replaces whatever the operator was reading with
 * an error line and the next successful poll brings it back: nothing was lost,
 * and the foreground content was thrown away anyway.
 *
 * This wrapper splits the one held failure on `hasContent` into two mutually
 * exclusive outputs — `blocking` (nothing on screen, render the full pane) and
 * `stale` (content on screen, render the inline notice). The exclusivity is the
 * contract: it is what makes "a call site cannot produce the fourth
 * combination" a property of the module rather than a convention five authors
 * are each asked to remember.
 *
 * `hasContent` is a parameter, not a predicate, because what counts as
 * "something to lose" legitimately differs per surface (spec D9: the board's
 * content is the rows the read produced, never the filtered projection). Five
 * call sites computing `hasContent` is not divergence; five call sites each
 * deciding what to render from `isError` is, and that is the defect.
 *
 * The query shapes below are query-core's, not inventions. The fourth is the
 * one this ticket exists for:
 *   never failed         → { isError: false, error: null, isFetching: false }
 *   failed, idle         → { isError: true,  error: E,    isFetching: false }
 *   failed, refetching   → { isError: false, error: null, isFetching: true  }
 *   failed over data     → { isError: true,  error: E,    isFetching: true  }
 */

import { renderHook } from "@testing-library/react";

import { usePolledReadFailure } from "../usePolledReadFailure";

type Query = { isError: boolean; error: unknown; isFetching: boolean };

const NEVER_FAILED: Query = { isError: false, error: null, isFetching: false };
const PENDING: Query = { isError: false, error: null, isFetching: true };
const FAILED: Query = { isError: true, error: null, isFetching: false };
const FAILED_OVER_DATA: Query = { isError: true, error: null, isFetching: true };

type Props<TSubject> = { query: Query; subject: TSubject; hasContent: boolean };

function split<TSubject>(initialProps: Props<TSubject>) {
  return renderHook(
    ({ query, subject, hasContent }: Props<TSubject>) =>
      usePolledReadFailure(query, subject, hasContent),
    { initialProps },
  );
}

describe("usePolledReadFailure", () => {
  describe("R0-1 — a query that has never failed justifies neither pane", () => {
    it.each([true, false])("reports nothing with hasContent %s", (hasContent) => {
      const { result } = split({ query: NEVER_FAILED, subject: "init1", hasContent });

      expect(result.current).toEqual({ blocking: null, stale: null });
    });

    it("reports nothing while the first read is still in flight", () => {
      // The loading state belongs to the caller's own `isPending` branch. A
      // wrapper that treated "no data yet" as a failure would render the error
      // pane for the whole of the first read.
      const { result } = split({ query: PENDING, subject: "init1", hasContent: false });

      expect(result.current).toEqual({ blocking: null, stale: null });
    });
  });

  describe("R0-2 — the two outputs are mutually exclusive by construction", () => {
    it("blocks when the read that failed had nothing on screen", () => {
      const error = new Error("the initiative's tickets could not be read");

      const { result } = split({
        query: { ...FAILED, error },
        subject: "init1",
        hasContent: false,
      });

      expect(result.current.blocking).toEqual({ error });
      expect(result.current.stale).toBeNull();
    });

    it("goes stale when the read that failed had content on screen", () => {
      // The regime this ticket exists for: query-core reports a failed poll
      // over good data as isError true WITH the next fetch already in flight.
      const error = new Error("the initiative's tickets could not be read");

      const { result } = split({
        query: { ...FAILED_OVER_DATA, error },
        subject: "init1",
        hasContent: true,
      });

      expect(result.current.stale).toEqual({ error });
      expect(result.current.blocking).toBeNull();
    });

    it("carries the error value through on both branches, falsy included", () => {
      // `error` is whatever was thrown and nothing guarantees it is truthy. The
      // branch is justified by the failure's existence, not by its payload
      // being inspectable; `describeError`'s fallback supplies the words. A
      // wrapper returning `error && {error}` falls through to a branch that
      // claims health nobody asked about.
      const blocking = split({ query: FAILED, subject: "x", hasContent: false });
      const stale = split({ query: FAILED_OVER_DATA, subject: "x", hasContent: true });

      expect(blocking.result.current.blocking).toEqual({ error: null });
      expect(stale.result.current.stale).toEqual({ error: null });
    });

    it("never returns both, over every combination of the four inputs", () => {
      // The whole reason this is a module. Asserted as a property rather than
      // as four cases, because the claim AC6 rests on is "no call site can
      // produce the fourth combination", and that is a statement about the
      // input space.
      const queries: [string, Query][] = [
        ["never failed", NEVER_FAILED],
        ["pending", PENDING],
        ["failed", FAILED],
        ["failed over data", FAILED_OVER_DATA],
      ];

      for (const [label, query] of queries) {
        for (const hasContent of [true, false]) {
          const { result } = split({ query, subject: "init1", hasContent });
          const { blocking, stale } = result.current;

          expect({ label, hasContent, both: Boolean(blocking && stale) }).toEqual({
            label,
            hasContent,
            both: false,
          });
        }
      }
    });

    it("moves the held failure between the two outputs when content arrives or leaves", () => {
      // `hasContent` is read on every render, not latched at the failure. A
      // failure held while the pane was empty must become the inline notice the
      // moment a later render has rows — otherwise the operator sees the full
      // alert pane over content that is on screen behind it.
      const error = new Error("boom");
      const { result, rerender } = split({
        query: { ...FAILED, error },
        subject: "init1",
        hasContent: false,
      });
      expect(result.current.blocking).toEqual({ error });

      rerender({ query: { ...FAILED, error }, subject: "init1", hasContent: true });

      expect(result.current).toEqual({ blocking: null, stale: { error } });
    });
  });

  describe("R0-3 — the failure survives the operator's own refetch", () => {
    it("still reports the prior failure while the retry is in flight", () => {
      // Delegated to useHeldFailure, pinned here because this wrapper is what
      // the four surfaces call: a wrapper that re-derived the failure from
      // `query.isError` would drop it on the click (lg-milestone-that-1435's
      // defect) and no test of useHeldFailure would notice.
      const error = new Error("the plan endpoint did not answer");
      const { result, rerender } = split({
        query: { ...FAILED, error },
        subject: "init1",
        hasContent: false,
      });

      rerender({ query: PENDING, subject: "init1", hasContent: false });

      expect(result.current.blocking).toEqual({ error });
      expect(result.current.stale).toBeNull();
    });

    it("clears once a read settles without failing", () => {
      const { result, rerender } = split({
        query: { ...FAILED, error: new Error("boom") },
        subject: "init1",
        hasContent: false,
      });

      rerender({ query: NEVER_FAILED, subject: "init1", hasContent: true });

      expect(result.current).toEqual({ blocking: null, stale: null });
    });
  });

  describe("R0-4 — the failure is withheld from a different subject", () => {
    it("reports nothing for a new subject whose own read is still in flight", () => {
      // The board and the plan page key on `initiativeId`, the head row on
      // `entryId`. An unkeyed hold renders one initiative's failure under
      // another's name, which is the defect lg-milestone-that-1434's review
      // found in GateStudioPanel.
      const error = new Error("init1 could not be read");
      const { result, rerender } = split({
        query: { ...FAILED, error },
        subject: "init1",
        hasContent: false,
      });

      rerender({ query: PENDING, subject: "init2", hasContent: false });

      expect(result.current).toEqual({ blocking: null, stale: null });
    });

    it("reports the new subject's own failure once it has one", () => {
      const first = new Error("init1 could not be read");
      const second = new Error("init2 could not be read");
      const { result, rerender } = split({
        query: { ...FAILED, error: first },
        subject: "init1",
        hasContent: false,
      });

      rerender({ query: PENDING, subject: "init2", hasContent: false });
      rerender({ query: { ...FAILED, error: second }, subject: "init2", hasContent: false });

      expect(result.current.blocking).toEqual({ error: second });
    });
  });
});

/**
 * lg-milestone-that-2187 test-break — the subject contract, which R0's own risk
 * analysis names and no test pinned.
 *
 * `useHeldFailure` compares subjects with `Object.is` and its docblock says so:
 * "callers pass a primitive — a slug, or the constant a query key is pinned to".
 * The four call sites honour it (`initiativeId`, `entryId`, `"mcp-telemetry"`).
 * A fifth that passes a freshly-built object — `{ id }`, a filter object, an
 * array of ids — type-checks, renders correctly on the failing render itself,
 * and then loses the guarantee R0-3 exists for, because the next render's
 * subject is a different reference.
 *
 * That is worth a test rather than a comment because of WHICH guarantee it
 * breaks and how quietly: the failure is withheld exactly in the regime the
 * operator's own Retry produces (`isError` false, `isFetching` true), so the
 * pane unmounts into the skeleton and takes the reason with it. That is
 * lg-milestone-that-1435's defect, reappearing through a compliant-looking API
 * on a surface that called the shared hook correctly in every other respect.
 */
describe("the subject must be a primitive (R0's stated risk)", () => {
  it("holds the failure across a refetch when the subject is a primitive", () => {
    // The control for the test below: same sequence, primitive subject. Without
    // this pair the next test proves only that something returned null.
    const error = new Error("the initiative's tickets could not be read");
    const { result, rerender } = split({
      query: { ...FAILED, error },
      subject: "init1",
      hasContent: false,
    });

    rerender({ query: PENDING, subject: "init1", hasContent: false });

    expect(result.current.blocking).toEqual({ error });
  });

  it("LIMIT — an object subject rebuilt each render drops the failure on the retry", () => {
    // Identical sequence, identical values, a new object each render. The
    // failure is reported on the render that failed, and withheld the moment the
    // retry is in flight — so the operator's click empties the pane.
    const error = new Error("the initiative's tickets could not be read");
    const { result, rerender } = split<{ id: string }>({
      query: { ...FAILED, error },
      subject: { id: "init1" },
      hasContent: false,
    });
    expect(result.current.blocking).toEqual({ error });

    rerender({ query: PENDING, subject: { id: "init1" }, hasContent: false });

    expect(result.current).toEqual({ blocking: null, stale: null });
  });
});
