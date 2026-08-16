/**
 * The flex-grid page against the races its layout writes can lose.
 *
 * Separate from `ViewPageGrid.test.tsx` because these are not acceptance
 * criteria: nothing here fails, nothing here toasts, and every layout involved
 * is one the server accepts. What they pin is *ordering* — a read of the view
 * resolving around a write of it, and two pointers holding two dividers of one
 * split — where the wrong order destroys a pane or strands a capture without
 * ever raising an error. The fixtures and the DOM contract are
 * `test/gridHarness`, shared with the criteria file.
 */

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { viewsKeys, type ViewSummary } from "../../lib/viewsApi";
import {
  SLUG,
  at,
  installGridHarness,
  childrenOf,
  containersOf,
  control,
  divider,
  lastLayout,
  mockFetchView,
  mockUpdateView,
  pairLayout,
  pointerDown,
  pointerMove,
  pointerUp,
  renderGrid,
  settle,
  sizeOf,
  storePatch,
  storedView,
  testClient,
  tripleLayout,
  type Json,
} from "../../test/gridHarness";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

jest.mock("../../components/TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: () => <div data-testid="fake-terminal" />,
}));

installGridHarness();

describe("a read of this view older than a write cannot land on top of it", () => {
  it("keeps the pane a split opened when a refetch that started first comes back", async () => {
    /*
     * The view query refetches on window focus, and react-query orders a fetch
     * resolving against a `setQueryData` not at all. So a GET issued before the
     * PATCH and resolving after it puts the pre-edit layout back under the same
     * key — invisibly, because every layout involved is one the server accepts
     * and nothing fails.
     *
     * The damage is done by the *next* edit: the write path composes its body
     * from the cache, so it PATCHes the reverted layout back and the pane the
     * split opened is deleted server-side, with no toast, from a screen that
     * showed it a moment ago.
     */
    const user = userEvent.setup();
    const qc = testClient();
    const { container } = renderGrid(pairLayout(), qc);
    await screen.findByTestId("view-host");

    // What a read issued right now resolves with, however long it takes to come
    // back: `mockUpdateView` replaces `stored` rather than editing it.
    const beforeEdit = storedView();
    let land: (record: ViewSummary) => void = () => {};
    mockFetchView.mockImplementation(
      () =>
        new Promise<ViewSummary>((resolve) => {
          land = resolve;
        }),
    );
    // Tabbing away and back mid-save. Left open on purpose.
    const refetch = qc.refetchQueries({ queryKey: viewsKeys.view(SLUG, "v-grid") }).catch(() => {});

    await user.click(control(container, "n-1", "split-horizontal"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(Object.keys(containersOf(lastLayout()))).toHaveLength(3);

    // The stale read lands, carrying the two-container layout from before.
    await act(async () => {
      land(beforeEdit);
      await refetch;
    });

    // Three panes on screen, not two.
    expect(container.querySelectorAll("[data-container-id]")).toHaveLength(3);

    // And the next unrelated edit is composed from a layout that still has them.
    mockFetchView.mockImplementation(async () => storedView());
    await user.click(control(container, "n-2", "split-vertical"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(2));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(Object.keys(containersOf(layout))).toHaveLength(4);
  });

  it("keeps it when the refetch started while the PATCH was still open", async () => {
    // The other half of the window: a read issued *after* the write went out
    // still saw the server from before it, and still lands afterwards. Only a
    // read issued once the PATCH has come back can be trusted, because only
    // then has the server applied it.
    const user = userEvent.setup();
    const qc = testClient();
    const { container } = renderGrid(pairLayout(), qc);
    await screen.findByTestId("view-host");

    const beforeEdit = storedView();
    let landPatch: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockImplementation(
      (_slug, _viewId, patch) =>
        new Promise<ViewSummary>((resolve) => {
          landPatch = resolve;
          storePatch(patch);
        }),
    );

    await user.click(control(container, "n-1", "split-horizontal"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));

    // Tabbing away and back with the PATCH still in flight.
    let landRead: (record: ViewSummary) => void = () => {};
    mockFetchView.mockImplementation(
      () =>
        new Promise<ViewSummary>((resolve) => {
          landRead = resolve;
        }),
    );
    const refetch = qc.refetchQueries({ queryKey: viewsKeys.view(SLUG, "v-grid") }).catch(() => {});

    await act(async () => {
      landPatch(storedView());
    });
    await settle();
    await act(async () => {
      landRead(beforeEdit);
      await refetch;
    });

    expect(container.querySelectorAll("[data-container-id]")).toHaveLength(3);

    mockFetchView.mockImplementation(async () => storedView());
    mockUpdateView.mockImplementation(async (_slug, _viewId, patch) => storePatch(patch));
    await user.click(control(container, "n-2", "split-vertical"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(2));
    assertServerAcceptableLayout(lastLayout());
    expect(Object.keys(containersOf(lastLayout()))).toHaveLength(4);
  });
});

describe("two dividers of one split are two gestures", () => {
  /** A pointer event carrying its own id, rather than the suite's single one. */
  function press(el: HTMLElement, pointerId: number, x: number) {
    fireEvent.pointerDown(el, { pointerId, button: 0, buttons: 1, ...at("x", x) });
  }
  function move(el: HTMLElement, pointerId: number, x: number) {
    fireEvent.pointerMove(el, { pointerId, buttons: 1, ...at("x", x) });
  }
  function release(el: HTMLElement, pointerId: number, x: number) {
    fireEvent.pointerUp(el, { pointerId, buttons: 0, ...at("x", x) });
  }

  it("does not let a second touch take over the first one's drag", async () => {
    /*
     * Two fingers, two handles, one split. Under a single drag slot the second
     * press overwrites the first's gap and origin, so the first finger's moves
     * resize the *other* pair from a starting point 250px away — and the second
     * finger's own release finds nothing to commit and never hands the capture
     * back, leaving the page swallowing pointer events until it is reloaded.
     */
    const releaseSpy = jest.spyOn(Element.prototype, "releasePointerCapture");
    const { container } = renderGrid(tripleLayout());
    await screen.findByTestId("view-host");
    const first = divider(container, "n-root", 0);
    const second = divider(container, "n-root", 1);

    press(first, 1, 500);
    press(second, 2, 750);

    // Finger 1 nudges its own divider right, and lets go.
    move(first, 1, 560);
    release(first, 1, 560);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));

    const moved = childrenOf(lastLayout().root as Json);
    assertServerAcceptableLayout(lastLayout());
    // Gap 0's pair paid for the move; the third pane, which gap 1 divides, did not.
    expect(moved[0].size as number).toBeCloseTo(0.56, 6);
    expect(moved[2].size as number).toBeCloseTo(0.25, 6);

    // Finger 2's release is its own gesture: it stores something, and it gives
    // the capture back.
    release(second, 2, 800);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(2));
    assertServerAcceptableLayout(lastLayout());
    expect(childrenOf(lastLayout().root as Json)[2].size as number).toBeLessThan(0.25);
    expect(releaseSpy.mock.calls.map(([id]) => id)).toEqual(expect.arrayContaining([1, 2]));
    releaseSpy.mockRestore();
  });

  it("ignores moves from a pointer that is not the one holding the divider", async () => {
    // Capture routes every later event to the handle that took it, so a second
    // pointer crossing that handle delivers moves against a drag it is no part
    // of — and each one would drag the panes from the wrong origin.
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    press(handle, 1, 500);
    move(handle, 2, 900);
    await settle();

    // The stray pointer moved nothing.
    expect(sizeOf(container, "n-1")).toBeCloseTo(0.5, 6);
    release(handle, 1, 700);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(childrenOf(lastLayout().root as Json)[0].size as number).toBeCloseTo(0.7, 6);
  });

  it("does not let a cancel on one handle commit an unsaved nudge on another", async () => {
    // An arrow-key adjustment waits on the keyboard's own settle. A shared
    // pending slot lets a `pointercancel` anywhere in the split write it out
    // under a different gap's gesture.
    const { container } = renderGrid(tripleLayout());
    await screen.findByTestId("view-host");
    const first = divider(container, "n-root", 0);
    const second = divider(container, "n-root", 1);

    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(mockUpdateView).not.toHaveBeenCalled();

    press(second, 2, 750);
    fireEvent.pointerCancel(second, { pointerId: 2, ...at("x", 750) });
    await settle();

    // Gap 1's cancel had nothing of its own to store, and gap 0's nudge is
    // still the keyboard's to commit.
    expect(mockUpdateView).not.toHaveBeenCalled();
    fireEvent.keyUp(first, { key: "ArrowRight" });
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const children = childrenOf(lastLayout().root as Json);
    expect(children[0].size as number).toBeGreaterThan(0.5);
    expect(children[2].size as number).toBeCloseTo(0.25, 6);
  });

  it("stores the resize even when releasing the capture throws", async () => {
    // `releasePointerCapture` throws on a pointer the element no longer holds —
    // which is exactly what an interrupted gesture is. Released before the
    // commit, that throw drops the user's drag without a word.
    const releaseSpy = jest
      .spyOn(Element.prototype, "releasePointerCapture")
      .mockImplementation(() => {
        throw new Error("InvalidPointerId");
      });
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    // The throw is the premise of this test, not its subject: it escapes the
    // handler synchronously and React reports it again on the window, and
    // neither of those is the question being asked.
    const swallow = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", swallow);
    pointerDown(handle, "x", 500);
    pointerMove(handle, "x", 700);
    try {
      pointerUp(handle, "x", 700);
    } catch {
      /* expected */
    }
    window.removeEventListener("error", swallow);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(childrenOf(lastLayout().root as Json)[0].size as number).toBeCloseTo(0.7, 6);
    releaseSpy.mockRestore();
  });
});
