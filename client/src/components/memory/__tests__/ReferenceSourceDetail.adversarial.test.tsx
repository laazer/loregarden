import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { api } from "../../../api/client";
import { ApiError } from "../../../api/http";
import type { LibraryDetail, LibraryRow } from "../../../api/referenceLibraryApi";
import { memoryLibrarySourceFromPath, memoryPath } from "../../../lib/appNavigation";
import { focusOnOperatorIntent } from "../../../lib/focusIntent";
import type { LibrarySort } from "../../../lib/referenceLibrary";
import { useToastStore } from "../../../state/toastStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import { ReferenceLibraryTable } from "../ReferenceLibraryTable";
import { ReferenceSourceDetail } from "../ReferenceSourceDetail";

/**
 * The panel's focus behaviour under the states `ReferenceSourceDetail.test.tsx`
 * cannot see, for lg-milestone-that-2205.
 *
 * Three gaps, each with its own reason for being invisible to the designed
 * suite rather than merely absent from it:
 *
 * 1. **A refetch while the operator is standing on the heading.** Every case in
 *    the designed suite reads the outcome off `document.activeElement`, and that
 *    instrument is blind in exactly one place: when the element the effect would
 *    focus is *already* `document.activeElement`, a `focus()` call changes
 *    nothing it can observe. "The operator Tabs into the panel" is the ticket's
 *    own wording and the heading is the panel's first stop, so this is not a
 *    corner — it is the reported scenario with the Tab key pressed once. The
 *    cases below count `focus()` calls instead, which is the only instrument
 *    that can tell "declined" from "permitted but invisible".
 *
 * 2. **A 404 refetch, then recovery.** AC-S7 pins the 404 itself. What nothing
 *    pins is the read *after* it: the 404 drops `source.isSuccess`, and the
 *    recovery raises it again, so the panel's effect genuinely re-enters with
 *    the same `sourceId`. That is the only path in the app that exercises the
 *    latch's "stays spent across a `ready` dip" branch, and it is reachable by
 *    this ticket's own alt-tab scenario over a flaky connection.
 *
 * 3. **Two activations before either lands.** The rule holds one slot, so two
 *    row clicks in quick succession are a real ordering question with a wrong
 *    answer available: focus the source the operator has already navigated away
 *    from. `focusIntent.adversarial.test.ts` pins the rule's side of this; this
 *    pins the panel's.
 *
 * STATED LIMIT, measured rather than assumed. The three focus-call cases in (1)
 * kill the likeliest wrong implementation there is — the old `[source.data]`
 * effect left in place beside the new hook — and they kill it where the
 * `activeElement` instrument cannot. What they cannot kill is a rule that
 * permits a move to an element already holding focus, because the hook's
 * `[key, ready]` deps mean the rule is never consulted a second time for one
 * source: that state is unreachable through the fix as specified rather than
 * merely untested. Those cases are therefore regression cover for the
 * dependency list, and they stop being so if the deps ever regress — which is
 * exactly when they would be needed.
 */

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const DEEP = deepSource as unknown as LibraryDetail;
const SOURCE_ID = DEEP.source.id;
const SORT: LibrarySort = { key: "name", dir: "ascending" };

function changed(detail: LibraryDetail = DEEP): LibraryDetail {
  return { ...detail, record: `${detail.record}\n\nA later pass added this line.` };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function row(overrides: Partial<LibraryRow>): LibraryRow {
  return { ...DEEP.source, ...overrides };
}

const onClose = jest.fn();

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel(sourceId = SOURCE_ID) {
  const client = makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[memoryPath("library", sourceId)]}>
          <ReferenceSourceDetail sourceId={sourceId} workspace="loregarden" position={null} onClose={onClose} />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

function SiblingTab({ rows }: { rows: LibraryRow[] }) {
  const location = useLocation();
  const selectedId = memoryLibrarySourceFromPath(location.pathname);
  return (
    <>
      <ReferenceLibraryTable
        rows={rows}
        sort={SORT}
        onSort={() => {}}
        selectedId={selectedId}
        search=""
        workspace="loregarden"
      />
      {selectedId ? (
        <ReferenceSourceDetail
          key={selectedId}
          sourceId={selectedId}
          workspace="loregarden"
          position={null}
          onClose={onClose}
        />
      ) : null}
    </>
  );
}

function renderSiblings(rows: LibraryRow[]) {
  const client = makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[memoryPath("library")]}>
          <SiblingTab rows={rows} />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

const heading = () => document.getElementById("rl-detail-heading") as HTMLElement;
const backButton = () => screen.getByRole("button", { name: /Back to sources/ });

/**
 * Counts every `focus()` the panel makes, on any element, from this point on.
 *
 * Spying the prototype rather than one node is deliberate: it catches a move to
 * an element the test did not think to watch, and it catches a move to the
 * element that already holds focus — which is the whole reason this file exists.
 * It calls through, so focus really moves and the panel behaves as it would in
 * the browser.
 */
function watchFocus(): jest.SpyInstance {
  return jest.spyOn(HTMLElement.prototype, "focus");
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.referenceSource.mockReset();
  useToastStore.setState({ toasts: [] });
  Element.prototype.scrollIntoView = jest.fn();
});

afterEach(() => {
  focusManager.setEventListener(() => () => {});
  focusManager.setFocused(undefined);
  // One module-level slot in the rule; a stamp left behind would arm the next
  // case's panel for a click it never made.
  focusOnOperatorIntent("drain", null);
  jest.restoreAllMocks();
});

describe("a refetch while the operator is standing on the heading", () => {
  it("makes no focus call at all when a changed payload arrives", async () => {
    // Blind to `document.activeElement`: the heading already holds focus, so a
    // re-fired effect leaves the one thing the designed suite measures exactly
    // as it was. Counting calls is what distinguishes "the rule declined" from
    // "the rule fired and nothing looked different".
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValueOnce(changed());
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    heading().focus();
    expect(heading()).toHaveFocus();
    const focused = watchFocus();

    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    expect(focused).not.toHaveBeenCalled();
    expect(heading()).toHaveFocus();
  });

  it("makes no focus call when the window regains focus", async () => {
    // The literal reported path — alt-tab away, come back — with the operator's
    // keyboard on the panel's own heading rather than on a control beside it.
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValue(changed());
    renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    heading().focus();
    const focused = watchFocus();

    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));
    await act(async () => {});

    expect(focused).not.toHaveBeenCalled();
  });

  it("makes no focus call on a refetch with the operator inside the panel's body", async () => {
    // Deeper than the heading: the operator has Tabbed to the ticket filter,
    // which is where AC-S11's 48 tab stops are actually spent. Nothing may move.
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValueOnce(changed());
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const filter = screen.getByLabelText("Filter this source's tickets");
    filter.focus();
    const focused = watchFocus();

    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    expect(focused).not.toHaveBeenCalled();
    expect(filter).toHaveFocus();
  });
});

describe("a 404 refetch and the read that recovers from it", () => {
  it("moves no focus on the way into the missing state or on the way out of it", async () => {
    // AC-S7 covers the 404. This covers the read after it, which is the only
    // path in the app where the panel's `ready` flag genuinely dips and returns
    // for one `sourceId` — the latch's "stays spent" branch, reachable by this
    // ticket's own alt-tab scenario on a connection that drops a request.
    mockApi.referenceSource.mockResolvedValueOnce(DEEP);
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const back = backButton();
    back.focus();
    const focused = watchFocus();

    mockApi.referenceSource.mockRejectedValueOnce(new ApiError(404, "no such source"));
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await screen.findByText("This source is no longer in the library.");
    expect(focused).not.toHaveBeenCalled();
    expect(back).toHaveFocus();

    // It came back — a renamed branch, a restarted server, a dropped request.
    mockApi.referenceSource.mockResolvedValue(changed());
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    // The operator asked for this source once, a long time ago. A recovery is
    // not a second activation, and the latch is what remembers that.
    expect(focused).not.toHaveBeenCalled();
    expect(back).toHaveFocus();
  });

  it("honours a still-live intent when a cold 404 recovers, because nothing consumed it", async () => {
    // The mirror of the case above, and the one that proves the latch's `!ready`
    // early return is doing work at the component level. Here the *cold* load
    // 404s, so `source.isSuccess` is never true and the effect never reaches the
    // rule — neither the latch nor the stamp is spent. When the read recovers,
    // the operator is still standing on the row they clicked and still inside
    // `FOCUS_INTENT_MS`, so this is AC-S5(a) with a failed read in the middle and
    // the move is owed.
    //
    // Written as its own case because the obvious reading of AC-S6's
    // "consumed on the first attempt" is that this should decline, and it should
    // not: a read that never succeeded made no attempt. An implementation that
    // spent the latch on the error render would leave the operator whose source
    // just loaded on the row link, which is R6 exactly.
    mockApi.referenceSource.mockRejectedValueOnce(new ApiError(404, "no such source"));
    const { client } = renderSiblings([row({ id: SOURCE_ID, name: "openharness" })]);

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);
    await screen.findByText("This source is no longer in the library.");
    // The operator has not moved: the row they activated still holds focus, and
    // is still the element the intent was armed from.
    expect(link).toHaveFocus();

    mockApi.referenceSource.mockResolvedValue(DEEP);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });

    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));
    expect(heading()).toHaveFocus();
  });

  it("declines that recovery once the operator has moved the keyboard", async () => {
    // The same path with the one thing that changes the answer: by the time the
    // read recovers the operator is somewhere of their own. The intent was real
    // and is now stale, and arm (a)'s element check is what reads the difference.
    mockApi.referenceSource.mockRejectedValueOnce(new ApiError(404, "no such source"));
    const { client } = renderSiblings([row({ id: SOURCE_ID, name: "openharness" })]);

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);
    await screen.findByText("This source is no longer in the library.");

    const sortControl = screen.getByRole("button", { name: /Source/ });
    sortControl.focus();
    const focused = watchFocus();

    mockApi.referenceSource.mockResolvedValue(DEEP);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    expect(focused).not.toHaveBeenCalled();
    expect(sortControl).toHaveFocus();
  });
});

describe("two activations before either read lands", () => {
  it("focuses the source the operator ended on, not the one they left", async () => {
    // One slot in the rule means this has a wrong answer available: honour the
    // first click and focus a heading for a source that is no longer on screen.
    const first = deferred<LibraryDetail>();
    const second = deferred<LibraryDetail>();
    const beluga = { ...DEEP, source: { ...DEEP.source, id: "second-id", name: "beluga" } };
    mockApi.referenceSource.mockImplementation((id: string) =>
      id === SOURCE_ID ? first.promise : second.promise,
    );
    renderSiblings([row({ id: SOURCE_ID, name: "openharness" }), row({ id: "second-id", name: "beluga" })]);

    await userEvent.click(screen.getByRole("link", { name: "openharness" }));
    const secondLink = screen.getByRole("link", { name: "beluga" });
    await userEvent.click(secondLink);
    expect(secondLink).toHaveFocus();

    await act(async () => {
      first.resolve(DEEP);
      second.resolve(beluga);
      await Promise.all([first.promise, second.promise]);
    });

    await waitFor(() => expect(heading()).toHaveTextContent("beluga"));
    expect(heading()).toHaveFocus();
  });

  it("does not focus a panel for a source the operator has navigated away from", async () => {
    // The same two clicks with only the abandoned source's read resolving. The
    // first click's stamp was replaced by the second, so nothing may move — and
    // the operator's focus sits on the second row, so arm (b) is shut too.
    const first = deferred<LibraryDetail>();
    mockApi.referenceSource.mockImplementation((id: string) =>
      id === SOURCE_ID ? first.promise : new Promise<LibraryDetail>(() => {}),
    );
    renderSiblings([row({ id: SOURCE_ID, name: "openharness" }), row({ id: "second-id", name: "beluga" })]);

    await userEvent.click(screen.getByRole("link", { name: "openharness" }));
    const secondLink = screen.getByRole("link", { name: "beluga" });
    await userEvent.click(secondLink);
    const focused = watchFocus();

    await act(async () => {
      first.resolve(DEEP);
      await first.promise;
    });

    expect(focused).not.toHaveBeenCalled();
    expect(secondLink).toHaveFocus();
  });
});
