import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { api } from "../../../api/client";
import { ApiError } from "../../../api/http";
import type { LibraryDetail, LibraryRow } from "../../../api/referenceLibraryApi";
import { tabbableWithin } from "../../../hooks/useDialogFocusTrap";
import { memoryLibrarySourceFromPath, memoryPath } from "../../../lib/appNavigation";
import type { LibrarySort } from "../../../lib/referenceLibrary";
import { useToastStore } from "../../../state/toastStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import { Button } from "../../ui/Button";
import { ReferenceLibraryTable } from "../ReferenceLibraryTable";
import { ReferenceSourceDetail } from "../ReferenceSourceDetail";

/**
 * Where focus goes in the reference source panel, and — the whole point of
 * lg-milestone-that-2205 — which reads are allowed to move it at all.
 *
 * The defect: the panel focused its `<h2 id="rl-detail-heading">` from an effect
 * keyed on `[source.data]`, the query payload's **object identity**. The query
 * client sets no `defaultOptions`, so react-query v5 defaults apply
 * (`staleTime: 0`, `refetchOnWindowFocus: true`): the operator opens a source,
 * Tabs into the panel, alt-tabs away, comes back, and the window-focus refetch
 * takes the focus they moved themselves. The docstring said "Focus moves to its
 * heading when it opens"; opening is exactly what the effect did not key on.
 *
 * The fix keys the move on a *permission rule* instead (`lib/focusIntent`, unit
 * tested in `lib/__tests__/focusIntent.test.ts`) driven by a one-shot latch
 * (`hooks/useFocusOnOperatorIntent`, unit tested beside it). A move is permitted
 * when either an unexpired operator intent names this `sourceId` and the element
 * that recorded it still holds focus, **or** `document.activeElement` is
 * `<body>`; declined otherwise.
 *
 * Two construction rules here are load-bearing, and both come from a premise in
 * the ticket that turned out to be false — "every successful refetch produces a
 * new object identity". It does not. react-query v5's default
 * `structuralSharing: true` routes through `replaceEqualDeep`, which returns the
 * *previous* reference on deep equality, so an identical refetch never fired the
 * defective effect either:
 *
 *   1. AC-S1(i)'s second read **differs in a field**. With identical bytes the
 *      case is green against the unmodified component and proves nothing.
 *   2. AC-S1(ii)'s byte-identical case sets **`structuralSharing: false`** on
 *      its client, so the library's dedupe cannot pass the test in the fix's
 *      place.
 *
 * The fixture is the largest real shape (`reference-library-deep-source.json`:
 * 71 tickets, 22 learnings, 24 passes, 41 unmatched references — all above
 * `PREVIEW = 12` / `PASS_PREVIEW = 5`, so the ticket filter and all three "show
 * all" toggles render).
 */

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const DEEP = deepSource as unknown as LibraryDetail;
const SOURCE_ID = DEEP.source.id;

/** The same source, with one field of the record different: a changed payload. */
function changed(detail: LibraryDetail = DEEP): LibraryDetail {
  return { ...detail, record: `${detail.record}\n\nA later pass added this line.` };
}

/** Byte-identical, but a fresh object graph every call. */
function freshClone(detail: LibraryDetail = DEEP): LibraryDetail {
  return JSON.parse(JSON.stringify(detail)) as LibraryDetail;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeClient(structuralSharing?: false) {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, ...(structuralSharing === false ? { structuralSharing: false } : {}) },
    },
  });
}

const onClose = jest.fn();

/** The panel alone, with its props under the test's control. */
function renderPanel({
  sourceId = SOURCE_ID,
  position = null,
  structuralSharing,
}: { sourceId?: string; position?: number | null; structuralSharing?: false } = {}) {
  const client = makeClient(structuralSharing);
  const Panel = ({ id, place }: { id: string; place: number | null }) => (
    <>
      <Button variant="plain" data-testid="outside">
        A control outside the panel
      </Button>
      <ReferenceSourceDetail sourceId={id} workspace="loregarden" position={place} onClose={onClose} />
    </>
  );
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[memoryPath("library", sourceId)]}>
        <Panel id={sourceId} place={position} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const rerenderWith = (next: { sourceId?: string; position?: number | null }) =>
    view.rerender(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[memoryPath("library", next.sourceId ?? sourceId)]}>
          <Panel id={next.sourceId ?? sourceId} place={next.position === undefined ? position : next.position} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  return { client, rerenderWith, ...view };
}

/**
 * The app's real shape for AC-S5(a): the source list and the panel are
 * **siblings mounted at the same time** (`ReferenceLibraryTab.tsx:244-253`), so
 * after a row click the row's `<a id="rl-source-{id}">` is still in the tree and
 * still holds focus. `document.activeElement` at the moment the panel's data
 * lands is that `<a>`, not `<body>` — which is why a `<body>`-only guard would
 * silently stop focusing the heading on the one path the operator actually
 * takes, while a direct-prop test that parks focus on `<body>` reports green.
 */
function row(overrides: Partial<LibraryRow>): LibraryRow {
  return { ...DEEP.source, ...overrides };
}

const SORT: LibrarySort = { key: "name", dir: "ascending" };

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
  return { client, ...render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[memoryPath("library")]}>
        <SiblingTab rows={rows} />
      </MemoryRouter>
    </QueryClientProvider>,
  ) };
}

const heading = () => document.getElementById("rl-detail-heading") as HTMLElement;
const backButton = () => screen.getByRole("button", { name: /Back to sources/ });

beforeEach(() => {
  jest.clearAllMocks();
  // `mockClear` leaves a `…Once` entry no test consumed queued for the next
  // test, which reads as a cold load that never resolves.
  mockApi.referenceSource.mockReset();
  useToastStore.setState({ toasts: [] });
  // jsdom implements neither, and AC-S8 asserts the panel calls neither.
  Element.prototype.scrollIntoView = jest.fn();
});

afterEach(() => {
  // Leaving the focus manager overridden would make the next suite's queries
  // think the window is blurred.
  focusManager.setEventListener(() => () => {});
  focusManager.setFocused(undefined);
});

describe("AC-S1 — an automatic refetch does not move focus", () => {
  it("AC-S1(i) leaves focus on the Back button across a successful refetch whose payload changed", async () => {
    // RED against the unmodified component: the second read's payload differs,
    // so `[source.data]` fires and the `<h2 id="rl-detail-heading">` takes the
    // focus the operator had moved to the Back button themselves.
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValueOnce(changed());
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const back = backButton();
    back.focus();
    expect(document.activeElement).toBe(back);

    // Driven through the query itself. No click, no keypress, no userEvent of
    // any kind after the focus move: the operator did nothing.
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    expect(document.activeElement).toBe(back);
    expect(heading()).not.toHaveFocus();
  });

  it("AC-S1(ii) does not focus the heading on a byte-identical refetch, with structuralSharing off", async () => {
    // `structuralSharing: false` is what makes this test the fix's rather than
    // the library's: with the default on, `replaceEqualDeep` hands back the
    // previous object reference and the defective effect never fires.
    mockApi.referenceSource.mockImplementation(() => Promise.resolve(freshClone()));
    const { client } = renderPanel({ structuralSharing: false });
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    // The payload's identity changed and nothing moved: the effect did not run.
    expect(document.activeElement).toBe(document.body);
  });
});

describe("AC-S6(d) — a window-focus refetch does not move focus", () => {
  it("leaves focus where the operator Tabbed to when the window regains focus", async () => {
    // The reported scenario, driven through react-query's own focus manager
    // rather than a hand-called refetch: open a source, Tab into the panel,
    // alt-tab away, come back.
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValue(changed());
    renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const back = backButton();
    back.focus();

    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));
    await act(async () => {});

    expect(document.activeElement).toBe(back);
  });
});

describe("AC-S5 — the three operator actions that do move focus", () => {
  it("AC-S5(a) focuses the new heading after a row click, while the clicked link is still mounted and had focus", async () => {
    // The acceptance gate. Nothing else in this suite distinguishes the correct
    // fix from a `<body>`-only one: removing the `recordFocusIntent` call at
    // the row `<Link>` must make this fail.
    const pending = deferred<LibraryDetail>();
    mockApi.referenceSource.mockReturnValue(pending.promise);
    const rows = [row({ id: SOURCE_ID, name: "openharness" }), row({ id: "other-id", name: "beluga" })];
    renderSiblings(rows);

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);

    // The state a `<body>`-only guard gets wrong: the panel's data is about to
    // land while the operator's own focus sits on the row they clicked.
    expect(link).toHaveFocus();
    expect(link.isConnected).toBe(true);

    await act(async () => {
      pending.resolve(DEEP);
      await pending.promise;
    });

    await waitFor(() => expect(heading()).toHaveFocus());
    expect(heading()).toHaveTextContent(DEEP.source.name);
    // Still there, and still the row it was: the fix permitted the move, it did
    // not become reachable by the list unmounting.
    expect(screen.getByRole("link", { name: "openharness" })).toBe(link);
  });

  it("AC-S5(b) focuses the heading on a cold deep link with focus on <body> and no stamp", async () => {
    mockApi.referenceSource.mockResolvedValue(DEEP);
    expect(document.activeElement).toBe(document.body);

    renderPanel();

    await waitFor(() => expect(heading()).toHaveFocus());
    expect(heading()).toHaveTextContent(DEEP.source.name);
  });

  it("AC-S5(b) moves focus to the new heading when the same mounted panel is given a different sourceId", async () => {
    // A contract of the component, not a path the app reaches:
    // `ReferenceLibraryTab.tsx:248` passes `key={selectedId}`, so in the app a
    // new id remounts the panel. Asserting the heading's *text* is what pins
    // "after the data arrived, not before" — the heading reads "Source" until
    // the data branch renders.
    const second = { ...DEEP, source: { ...DEEP.source, id: "second-id", name: "beluga" } };
    const pending = deferred<LibraryDetail>();
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockReturnValueOnce(pending.promise);
    const { rerenderWith } = renderPanel();
    await waitFor(() => expect(heading()).toHaveFocus());

    (document.activeElement as HTMLElement).blur();
    rerenderWith({ sourceId: "second-id" });

    // While the second read is in flight there is no heading text to focus to.
    expect(heading()).toHaveTextContent("Source");
    expect(document.activeElement).toBe(document.body);

    await act(async () => {
      pending.resolve(second);
      await pending.promise;
    });

    await waitFor(() => expect(heading()).toHaveTextContent("beluga"));
    expect(heading()).toHaveFocus();
  });

  it("AC-S5(c) focuses the heading after a successful Retry from the error pane", async () => {
    // A restore, not a theft: the operator pressed Retry, their control
    // unmounted on the error→data swap, and `<body>` is the state the browser
    // left behind. Reached through arm (b). This is also why the latch must not
    // be consumed while `!ready` — the cold load errored first.
    mockApi.referenceSource
      .mockRejectedValueOnce(new ApiError(500, "the source request failed"))
      .mockResolvedValue(DEEP);
    renderPanel();
    const retry = await screen.findByRole("button", { name: "Retry" });

    await userEvent.click(retry);

    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));
    expect(retry.isConnected).toBe(false);
    expect(heading()).toHaveFocus();
  });
});

describe("AC-S6 — the actions that do not move focus", () => {
  it("AC-S6(e) does not move focus on a list-poll re-render", async () => {
    mockApi.referenceSource.mockResolvedValue(DEEP);
    const { rerenderWith } = renderPanel({ position: 3 });
    await waitFor(() => expect(heading()).toHaveFocus());

    (document.activeElement as HTMLElement).blur();
    // The 60s list poll re-renders the tab, and with it the panel, with the
    // same props.
    rerenderWith({});
    await act(async () => {});

    expect(document.activeElement).toBe(document.body);
  });

  it("AC-S6(f) does not move focus on a router re-render that leaves sourceId unchanged", async () => {
    mockApi.referenceSource.mockResolvedValue(DEEP);
    const { rerenderWith } = renderPanel({ position: 3 });
    await waitFor(() => expect(heading()).toHaveFocus());

    (document.activeElement as HTMLElement).blur();
    rerenderWith({ position: 2 });
    await act(async () => {});

    expect(document.activeElement).toBe(document.body);
  });

  it("AC-S6(g) does not move focus when an in-panel control re-renders the panel", async () => {
    mockApi.referenceSource.mockResolvedValue(DEEP);
    renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const filter = screen.getByLabelText("Filter this source's tickets");
    await userEvent.type(filter, "lg-");
    expect(filter).toHaveFocus();

    const toggle = screen.getByRole("button", { name: `Show all ${DEEP.passes.length} passes` });
    await userEvent.click(toggle);

    // Both re-render the panel; neither is a read, and neither is an operator
    // asking for this source again.
    expect(screen.getByRole("button", { name: "Show the latest" })).toHaveFocus();
    expect(heading()).not.toHaveFocus();
  });

  it("AC-S6 leaves focus where the operator moved it when a cold load resolves late, with no stamp", async () => {
    // RED against the unmodified component: the first successful read focuses
    // the heading unconditionally, taking focus the operator moved while the
    // panel was still loading.
    const pending = deferred<LibraryDetail>();
    mockApi.referenceSource.mockReturnValue(pending.promise);
    renderPanel();

    const outside = screen.getByTestId("outside");
    outside.focus();
    expect(outside).toHaveFocus();

    await act(async () => {
      pending.resolve(DEEP);
      await pending.promise;
    });
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    expect(outside).toHaveFocus();
  });

  it("AC-S6 declines a stamped row click whose operator moved focus before the data landed, and does not retry it", async () => {
    const first = deferred<LibraryDetail>();
    const second = deferred<LibraryDetail>();
    mockApi.referenceSource.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { client } = renderSiblings([row({ id: SOURCE_ID, name: "openharness" })]);

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);
    expect(link).toHaveFocus();

    // The intent was real and is now stale: the operator asked, then moved the
    // keyboard before the fetch landed.
    const sortControl = screen.getByRole("button", { name: /Source/ });
    sortControl.focus();

    await act(async () => {
      first.resolve(DEEP);
      await first.promise;
    });
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));
    expect(sortControl).toHaveFocus();

    // The stamp was consumed on that first attempt, so a later successful read
    // does not get a second go at the move.
    second.resolve(changed());
    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    expect(sortControl).toHaveFocus();
  });
});

describe("AC-S7 — a source that disappears is said, not navigated away from", () => {
  it("replaces the body, keeps the head, moves no focus and does not close", async () => {
    mockApi.referenceSource.mockResolvedValueOnce(DEEP);
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const back = backButton();
    back.focus();
    mockApi.referenceSource.mockRejectedValue(new ApiError(404, "no such source"));

    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });

    await screen.findByText("This source is no longer in the library.");
    const panel = document.querySelector(".rl-detail") as HTMLElement;
    const body = document.querySelector(".rl-detail-body") as HTMLElement;

    // The body is replaced in full: nothing of the old payload is left to read
    // as current.
    expect(body.querySelectorAll(".rl-section")).toHaveLength(0);
    expect(body.querySelectorAll("h3")).toHaveLength(0);
    // The head is the only thing on screen saying *which* source went, so it
    // keeps the name, the state badge, the url and the summary.
    expect(heading()).toHaveTextContent(DEEP.source.name);
    expect(panel.querySelector(".rl-state-badge")).not.toBeNull();
    expect(within(panel).getByRole("link", { name: new RegExp(DEEP.source.url.replace(/^https?:\/\//, "")) })).toBeInTheDocument();
    expect(panel.querySelector(".rl-summary")).not.toBeNull();
    // The way out stays the Back button (a Shift+Tab from the heading, which it
    // precedes in DOM order) and Escape.
    expect(backButton()).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // Not a transition that moves focus, and not an error to describe.
    expect(back).toHaveFocus();
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

describe("AC-S8 — a successful refetch changes nothing the operator can see except the data", () => {
  it("keeps the expanded group, the filter string, the disclosure and the scroll", async () => {
    mockApi.referenceSource.mockResolvedValueOnce(DEEP).mockResolvedValueOnce(changed());
    const { client } = renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    // The interaction precedes the refetch, so AC-S1's no-interaction rule is
    // untouched.
    const toggle = screen.getByRole("button", { name: `Show all ${DEEP.passes.length} passes` });
    await userEvent.click(toggle);
    const expanded = screen.getByRole("button", { name: "Show the latest" });
    expect(expanded).toHaveAttribute("aria-expanded", "true");

    const filter = screen.getByLabelText("Filter this source's tickets");
    await userEvent.type(filter, "lg-milestone");
    const disclosure = document.querySelector("details.rl-details") as HTMLDetailsElement;
    await userEvent.click(within(disclosure).getByText(/matched no ticket/));
    expect(disclosure.open).toBe(true);

    const panel = document.querySelector(".rl-detail") as HTMLElement;
    const scrollBefore = panel.scrollTop;

    await act(async () => {
      await client.refetchQueries({ queryKey: ["reference-source", SOURCE_ID] });
    });
    await waitFor(() => expect(mockApi.referenceSource).toHaveBeenCalledTimes(2));

    expect(screen.getByRole("button", { name: "Show the latest" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Filter this source's tickets")).toHaveValue("lg-milestone");
    expect((document.querySelector("details.rl-details") as HTMLDetailsElement).open).toBe(true);
    expect(panel.scrollTop).toBe(scrollBefore);
    // A declined move is silent: no scroll, no toast, no announcement.
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts).toEqual([]);
    expect(panel.querySelectorAll("[aria-live]")).toHaveLength(0);
    // Nothing new binds to `source.isFetching`: the cold-load skeleton stays
    // limited to the cold load, so a background refetch is visually identical.
    expect(document.querySelector(".ui-skeleton")).toBeNull();
  });
});

describe("AC-S9 — the rendered output the fix must not change", () => {
  it("keeps the labelled section, the focusable heading, the Escape handler and the Back button", async () => {
    mockApi.referenceSource.mockResolvedValue(DEEP);
    renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const panel = document.querySelector(".rl-detail") as HTMLElement;
    expect(panel).toHaveAttribute("aria-labelledby", "rl-detail-heading");
    expect(heading().tagName).toBe("H2");
    expect(heading().tabIndex).toBe(-1);
    // The Back button precedes the heading in DOM order, which is what makes a
    // Shift+Tab from the heading the way out.
    expect(backButton().compareDocumentPosition(heading()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    heading().focus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps the error branch's role=alert and its Retrying… binding", async () => {
    mockApi.referenceSource.mockRejectedValue(new ApiError(500, "the source request failed"));
    renderPanel();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/The source did not load/);
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeEnabled();
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain("Source did not load"),
    );
  });
});

/**
 * Measured on 2026-11-06 over `reference-library-deep-source.json` (71 tickets,
 * 22 learnings, 24 passes): the panel offers 48 tab stops and the Passes "show
 * all" toggle is the last of them. So an unasked-for focus move to the heading
 * costs the operator up to 48 Tab presses to get back to where they were, and
 * exactly 48 if they were reaching for that toggle. Re-measure when the panel's
 * controls change; these are a measurement, not a policy.
 */
const TAB_STOPS = 48;
const PASSES_TOGGLE_INDEX = 47;

describe("AC-S11 — the Tab distance the defect throws the operator back from", () => {
  it("measures the tabbables in the panel at the largest real shape", async () => {
    // Measured, not estimated: the whole cost of the defect is that a focus
    // move the operator did not ask for restarts their Tab position at the
    // heading, this many stops before the control they were reaching for.
    //
    // Re-measure these two numbers when the panel's controls change; they are a
    // measurement of this fixture (71 tickets, 22 learnings, 24 passes), not a
    // policy.
    mockApi.referenceSource.mockResolvedValue(DEEP);
    renderPanel();
    await waitFor(() => expect(heading()).toHaveTextContent(DEEP.source.name));

    const panel = document.querySelector(".rl-detail") as HTMLElement;
    const stops = tabbableWithin(panel);
    const passesToggle = screen.getByRole("button", { name: `Show all ${DEEP.passes.length} passes` });
    const index = stops.indexOf(passesToggle);

    expect(stops).toContain(passesToggle);
    expect({ stops: stops.length, passesToggleIndex: index }).toEqual({
      stops: TAB_STOPS,
      passesToggleIndex: PASSES_TOGGLE_INDEX,
    });
  });
});

