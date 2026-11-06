/**
 * lg-milestone-that-2197 — the hook's three ordered facts, in isolation.
 *
 * The landing is specified as one new answer on exactly one of three states the
 * hook's single guard collapses today (`useRestoreFocusOnSettle.ts:50-52`):
 *
 *   1. `!node`            — nothing was armed; this settle is not an operator's.
 *   2. `!node.isConnected` — the surface was replaced; THIS falls to `landing`.
 *   3. `node.disabled`     — still in the tree, still releasing; still the
 *                            right target, so return rather than move focus.
 *
 * Every oracle in `WorkflowMonitorView.focus.test.tsx` drives this through the
 * monitor pane, and two of the three facts are observable there. Fact 3 is not:
 * the monitor's Retry is bound `disabled={retry.pending}`, so the render that
 * releases `pending` re-enables the button in the same commit, and a settle
 * where the armed control is *connected and still disabled* cannot be produced
 * from that component at all. An implementation that collapses facts 2 and 3
 * back together —
 *
 *     if (!node) return;
 *     if (!node.isConnected || node.disabled) { landing?.()?.focus(); return; }
 *
 * — passes all 21 component oracles while moving focus off a control the
 * operator is still on. `pending` is a parameter here, and the node is this
 * file's own DOM, so the state is reachable and the mutation goes red.
 *
 * The three other callers (`BranchCleanupModal`, `BranchDeleteConfirmModal`,
 * `BranchWorktreesModal`) pass one argument, so the one-argument contract is
 * AC5's and is asserted here rather than left to their suites to notice.
 */
import { act, renderHook } from "@testing-library/react";

import { useRestoreFocusOnSettle } from "../useRestoreFocusOnSettle";

/**
 * A real control in the real document, because the hook reads `isConnected`,
 * `disabled` and `document.activeElement` — three facts a stub cannot carry.
 * `createElement`, not JSX: the theme gate's raw-`<button>` rule is about
 * rendered surfaces, and this is a fixture for a hook.
 */
function control(label: string) {
  const el = document.createElement("button");
  el.textContent = label;
  document.body.append(el);
  return el;
}

/**
 * One activation, start to settle: arm the control, go pending, let the caller
 * reshape the DOM the way the settle will find it, then release.
 *
 * `landing` is passed through exactly as the caller gave it — `undefined` for
 * the one-argument contract — so no case is tested against an interface the
 * other three callers do not use.
 */
function activation(landing?: () => HTMLElement | null) {
  const { result, rerender } = renderHook(
    ({ pending }: { pending: boolean }) => useRestoreFocusOnSettle(pending, landing),
    { initialProps: { pending: false } },
  );
  return {
    arm: (node: HTMLElement) => act(() => result.current(node)),
    start: () => rerender({ pending: true }),
    settle: () => rerender({ pending: false }),
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("the fact the settle is answered by", () => {
  it("consults no landing when nothing was armed", () => {
    // Fact 1, and the whole of AC3 at the unit level: a background poll never
    // calls the arming callback, so the landing must not even be read. <body>
    // is active here, as it is on a freshly loaded tab, which is why the
    // <body> guard cannot tell this case from an operator's own settle.
    const landing = jest.fn(() => control("Needs attention"));
    const { start, settle } = activation(landing);

    start();
    settle();

    expect(landing).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });

  it("hands focus back to the armed control, not the landing, when it is still operable", () => {
    // Fact 2 is false and fact 3 is false: lg-milestone-that-1435's restore is
    // the answer, and the landing is not an alternative to it.
    const elsewhere = control("Needs attention");
    const landing = jest.fn(() => elsewhere);
    const retry = control("Retry");
    const { arm, start, settle } = activation(landing);

    arm(retry);
    start();
    settle();

    expect(document.activeElement).toBe(retry);
    expect(landing).not.toHaveBeenCalled();
  });

  it("falls through to the landing when the armed control has left the tree", () => {
    // Fact 2, the one new answer. The detached node is ALSO `disabled` here,
    // because React unmounts the monitor's Retry with its last-rendered
    // `disabled={retry.pending}` still true — so this case is precisely the
    // one a `disabled`-first guard answers with fact 3 and never lands.
    const scope = control("Needs attention");
    const retry = control("Retry");
    const { arm, start, settle } = activation(() => scope);

    arm(retry);
    start();
    retry.disabled = true;
    retry.remove();
    settle();

    expect(document.activeElement).toBe(scope);
  });

  it("moves nothing when the armed control is still in the tree and still releasing", () => {
    // Fact 3, and the state `WorkflowMonitorView` cannot produce. The control
    // is connected, so the surface the operator is on is still there; it is
    // disabled, so there is nothing to hand focus back to *yet*. Neither a
    // focus move nor a landing is allowed: the operator is mid-action on a
    // surface that has not been replaced.
    const scope = control("Needs attention");
    const landing = jest.fn(() => scope);
    const retry = control("Retry");
    retry.disabled = true;
    const { arm, start, settle } = activation(landing);

    arm(retry);
    start();
    settle();

    expect(landing).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });

  it("leaves focus the operator moved themselves, even though the surface is gone", () => {
    // The <body> guard is unconditional and covers the landing path too. The
    // call count is deliberately not asserted: the spec pins where `.focus()`
    // may be called, not whether the getter is read, so an oracle on the
    // getter would pin an implementation detail instead of the behaviour.
    const scope = control("Needs attention");
    const theirs = control("Somewhere they went");
    const retry = control("Retry");
    const { arm, start, settle } = activation(() => scope);

    arm(retry);
    start();
    retry.remove();
    theirs.focus();
    settle();

    expect(document.activeElement).toBe(theirs);
  });
});

describe("the landing's interface", () => {
  it("is optional, so a caller that passes none is unaffected", () => {
    // AC5. `BranchCleanupModal`, `BranchDeleteConfirmModal` and
    // `BranchWorktreesModal` call this hook with one argument and are not
    // edited by this ticket, so the detached-node settle they can reach must
    // stay a no-op rather than a crash.
    const retry = control("Delete worktree");
    const { arm, start, settle } = activation();

    arm(retry);
    start();
    retry.remove();

    expect(() => settle()).not.toThrow();
    expect(document.activeElement).toBe(document.body);
  });

  it("may answer null, which is a legal do-nothing rather than a crash", () => {
    // The landing is a getter because the element does not exist at activation
    // time; the honest answer when the replacement surface mounted nothing
    // focusable is `null`, and the hook has to take it.
    const retry = control("Retry");
    const { arm, start, settle } = activation(() => null);

    arm(retry);
    start();
    retry.remove();

    expect(() => settle()).not.toThrow();
    expect(document.activeElement).toBe(document.body);
  });

  it("answers every activation, not just the first one of a mount", () => {
    // The monitor can reach a second landing without remounting: a retry that
    // succeeds with nothing to report leaves `all.length === 0`, so the next
    // failed poll puts the same error pane back, and the retry after it lands
    // again. An implementation that latches "already landed" — a `useRef`
    // beside the arming one — is green on every oracle that drives a single
    // recovery, and silently stops working for the rest of the session.
    const scope = control("Needs attention");
    const landing = jest.fn(() => scope);
    const { arm, start, settle } = activation(landing);

    for (const label of ["Retry", "Retry again"]) {
      const retry = control(label);
      arm(retry);
      start();
      retry.disabled = true;
      retry.remove();
      settle();

      expect(document.activeElement).toBe(scope);
      // Back to where the browser leaves a keyboard operator when the surface
      // holding their focus is replaced, so the next settle is not declined by
      // the <body> guard instead of by the fact under test.
      scope.blur();
      expect(document.activeElement).toBe(document.body);
    }

    expect(landing).toHaveBeenCalledTimes(2);
  });

  it("is not consulted a second time by a settle nobody armed", () => {
    // Determinism across settles: the arming is consumed by the settle it
    // belongs to. An implementation that remembers the landing and re-reads it
    // on every later settle yanks focus off whatever the operator moved to
    // once per background poll, which on this surface is every 30 seconds.
    const first = control("Needs attention");
    const targets: HTMLElement[] = [first];
    const landing = jest.fn(() => targets[targets.length - 1]);
    const retry = control("Retry");
    const { arm, start, settle } = activation(landing);

    arm(retry);
    start();
    retry.remove();
    settle();

    expect(landing).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(first);

    // The second settle finds <body> active and a live landing to read, so
    // nothing but fact 1 is stopping it: a stored landing re-read here is
    // caught by both the count and where focus ends up.
    targets.push(control("Everything"));
    first.remove();
    expect(document.activeElement).toBe(document.body);

    start();
    settle();

    expect(landing).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(document.body);
  });
});
