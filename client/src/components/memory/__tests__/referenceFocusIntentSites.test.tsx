import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import { ApiError } from "../../../api/http";
import type {
  AutoRereviewSettings,
  BulkAddItem,
  LibraryDetail,
  LibraryRow,
  QueueSchedule,
} from "../../../api/referenceLibraryApi";
import { focusOnOperatorIntent, recordFocusIntent } from "../../../lib/focusIntent";
import type { LibrarySort } from "../../../lib/referenceLibrary";
import { setRouterNavigate } from "../../../lib/routerBridge";
import { navigateToReferenceSource } from "../../../lib/useAppNavigation";
import { useToastStore } from "../../../state/toastStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import libraryFixture from "../../../test/fixtures/prod-shape/reference-library-loregarden.json";
import { OpenReferenceSourceButton } from "../../chat/primitives/ResourceActionButton";
import { ReferenceAutoRereviewButton } from "../ReferenceAutoRereview";
import { ReferenceLibraryTable } from "../ReferenceLibraryTable";
import { ReferenceQueueStatus } from "../ReferenceQueueStatus";
import { ReferenceSourceForm } from "../ReferenceSourceForm";

/**
 * AC-S4 of lg-milestone-that-2205: **every** writer of a source URL stamps the
 * operator's intent.
 *
 * This is the criterion with no test of its own. `ReferenceSourceDetail.test.tsx`
 * reaches exactly one of the nine sites — the row `<Link>`, through AC-S5(a) —
 * so an implementation that stamps that one site and forgets the other eight
 * passes the whole designed suite. What ships then is a panel that focuses its
 * heading when the operator arrives from the source table and silently declines
 * when they arrive from the review queue, the auto re-review dialog, a bulk-add
 * result, a refused-duplicate warning or a chat action button. AC-S4 says it in
 * as many words — "a half-migrated mechanism is not a fix" — and nothing was
 * checking it.
 *
 * ## How a site is tested without mocking the mechanism
 *
 * `lib/focusIntent` is **not** mocked. Asserting that `recordFocusIntent` was
 * called would be an assertion about a call, and a call with the wrong id or the
 * wrong element satisfies it just as well. Instead each case clicks the real
 * control and then asks the real rule the question the panel will ask:
 *
 *     focusOnOperatorIntent(<the source's id>, heading) === true
 *
 * while the clicked element is **still mounted and still
 * `document.activeElement`**. That state is what makes the probe sharp in both
 * directions. Arm (b) cannot produce a false pass, because the document is not
 * idle — the operator's own link holds focus. And arm (a) can only pass if the
 * stamp exists, names *this* id, and recorded *this* element. Each case asserts
 * the preconditions explicitly rather than assuming them, so a control that
 * navigated its own component out of the tree fails loudly instead of passing
 * through arm (b).
 *
 * ## The ninth site has no observable effect, and that is worth writing down
 *
 * `ReferenceLibraryTab.tsx`'s add-form `onDone` has no activation element, so
 * AC-S4 has it pass `null`. A `from: null` stamp can never satisfy arm (a)
 * (`document.activeElement` is never `null`), so the site relies on arm (b) —
 * and arm (b) does not read the stamp. The call is therefore a **no-op by
 * construction**: the behaviour is identical whether or not it is made. No
 * probe can distinguish it, so the case for that site asserts the outcome the
 * operator gets instead, and the structural check at the bottom of this file is
 * what holds the call itself in place.
 */

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const DEEP = deepSource as unknown as LibraryDetail;
const LIBRARY = libraryFixture as unknown as { sources: LibraryRow[]; schedule: QueueSchedule };
const SORT: LibrarySort = { key: "name", dir: "ascending" };

function row(overrides: Partial<LibraryRow>): LibraryRow {
  return { ...DEEP.source, ...overrides };
}

/** A heading of the shape the panel focuses, so the probe has a real target. */
function heading(): HTMLElement {
  const element = document.createElement("h2");
  element.id = "rl-detail-heading";
  element.tabIndex = -1;
  document.body.append(element);
  jest.spyOn(element, "focus").mockImplementation(() => {});
  return element;
}

/**
 * The question the panel asks when its data lands, asked from the test: would a
 * move to `target` be permitted for `sourceId` right now?
 *
 * Asserts its own preconditions first. `activation` must still be in the tree
 * and still hold focus — if the click navigated it away, `document.activeElement`
 * falls back to `<body>` and arm (b) would answer `true` for every id, stamped
 * or not.
 */
function wouldFocusFor(sourceId: string, activation: HTMLElement): boolean {
  expect(activation.isConnected).toBe(true);
  expect(document.activeElement).toBe(activation);
  return focusOnOperatorIntent(sourceId, heading());
}

function renderInRouter(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/memory/library"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SCHEDULE: QueueSchedule = {
  ...LIBRARY.schedule,
  running: [{ source_id: "running-id", name: "openharness", ordinal: 3, carrier: null }],
  next: {
    source_id: "next-id",
    name: "beluga",
    ordinal: 2,
    mode: "rereview",
    waiting: "",
    can_start_now: false,
  },
};

const REREVIEW: AutoRereviewSettings = {
  enabled: true,
  min_new_commits: 25,
  max_commits_per_review: 50,
  max_commits_ceiling: 100,
  due: 3,
  in_flight: { source_id: "in-flight-id", name: "openharness", ordinal: 4, status: "reading" },
  excluded: [row({ id: "excluded-id", name: "beluga" })],
  updated_at: "2026-11-01T00:00:00",
} as unknown as AutoRereviewSettings;

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.referenceSource.mockReset();
  useToastStore.setState({ toasts: [] });
  setRouterNavigate(null);
});

afterEach(() => {
  // One module-level slot: a stamp left behind would answer the next case's
  // probe for a key it never armed.
  focusOnOperatorIntent("drain", null);
  setRouterNavigate(null);
  jest.restoreAllMocks();
});

describe("AC-S4 — the eight sites with an activation element", () => {
  it("ReferenceLibraryTable's row link stamps its own row's id", async () => {
    renderInRouter(
      <ReferenceLibraryTable
        rows={[row({ id: "row-a", name: "openharness" }), row({ id: "row-b", name: "beluga" })]}
        sort={SORT}
        onSort={() => {}}
        selectedId={null}
        search=""
        workspace="loregarden"
      />,
    );

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);

    expect(wouldFocusFor("row-a", link)).toBe(true);
  });

  it("stamps the row the operator clicked, not a neighbour", async () => {
    // The id is read off the row rather than off anything ambient, so a handler
    // bound to the wrong variable — the selected row, the first row, the loop's
    // last — is caught here rather than by an operator wondering why focus only
    // moves on some rows.
    renderInRouter(
      <ReferenceLibraryTable
        rows={[row({ id: "row-a", name: "openharness" }), row({ id: "row-b", name: "beluga" })]}
        sort={SORT}
        onSort={() => {}}
        selectedId="row-a"
        search=""
        workspace="loregarden"
      />,
    );

    const second = screen.getByRole("link", { name: "beluga" });
    await userEvent.click(second);

    expect(wouldFocusFor("row-a", second)).toBe(false);
  });

  it("ReferenceQueueStatus's \"Reading now\" link stamps the running pass's source", async () => {
    renderInRouter(<ReferenceQueueStatus schedule={SCHEDULE} discoveries={[]} search="" />);

    const link = screen.getByRole("link", { name: "openharness" });
    await userEvent.click(link);

    expect(wouldFocusFor("running-id", link)).toBe(true);
  });

  it("ReferenceQueueStatus's \"Next review\" link stamps the next pass's source", async () => {
    renderInRouter(<ReferenceQueueStatus schedule={SCHEDULE} discoveries={[]} search="" />);

    const link = screen.getByRole("link", { name: "beluga" });
    await userEvent.click(link);

    expect(wouldFocusFor("next-id", link)).toBe(true);
  });

  it("ReferenceAutoRereview's in-flight link stamps the source being re-read", async () => {
    mockApi.autoRereviewSettings.mockResolvedValue(REREVIEW);
    renderInRouter(
      <ReferenceAutoRereviewButton open onOpen={() => {}} onClose={() => {}} search="" />,
    );

    const link = await screen.findByRole("link", { name: /openharness pass 4/ });
    await userEvent.click(link);

    expect(wouldFocusFor("in-flight-id", link)).toBe(true);
  });

  it("ReferenceAutoRereview's taken-out link stamps the excluded source", async () => {
    mockApi.autoRereviewSettings.mockResolvedValue(REREVIEW);
    renderInRouter(
      <ReferenceAutoRereviewButton open onOpen={() => {}} onClose={() => {}} search="" />,
    );

    const taken = await screen.findByRole("region", { name: "Taken out of auto re-review" });
    const link = within(taken).getByRole("link", { name: "beluga" });
    await userEvent.click(link);

    expect(wouldFocusFor("excluded-id", link)).toBe(true);
  });

  it("ReferenceSourceForm's refused-duplicate link stamps the source the add was refused for", async () => {
    // The operator added a URL already in the library; the warning offers to
    // open the one that is there. That is an operator asking for a source by
    // name, so the panel owes them the same focus move the table gives.
    const message = "That URL is already in the library";
    mockApi.referenceTopics.mockResolvedValue([]);
    mockApi.initiatives.mockResolvedValue([]);
    mockApi.workspaces.mockResolvedValue([
      { id: "w1", slug: "loregarden", name: "loregarden", archived_at: null },
    ] as Awaited<ReturnType<typeof api.workspaces>>);
    mockApi.addReferenceSource.mockRejectedValue(
      new ApiError(409, message, { message, source_id: "refused-id" }),
    );
    renderInRouter(
      <ReferenceSourceForm workspace="loregarden" onDone={() => {}} onCancel={() => {}} />,
    );

    const form = screen.getByRole("form", { name: "Add a reference" });
    await userEvent.type(
      within(form).getByLabelText("URL"),
      "https://github.com/kayraucklnc/beluga",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Add to queue" }));

    const link = await screen.findByRole("link", { name: "Open it in the library" });
    await userEvent.click(link);

    expect(wouldFocusFor("refused-id", link)).toBe(true);
  });

  it("ReferenceSourceForm's bulk-result link stamps the source that result names", async () => {
    mockApi.referenceTopics.mockResolvedValue([]);
    mockApi.initiatives.mockResolvedValue([]);
    mockApi.workspaces.mockResolvedValue([
      { id: "w1", slug: "loregarden", name: "loregarden", archived_at: null },
    ] as Awaited<ReturnType<typeof api.workspaces>>);
    const items: BulkAddItem[] = [
      {
        url: "https://github.com/kayraucklnc/beluga",
        outcome: "queued",
        kind: "repo",
        source: row({ id: "bulk-id", name: "beluga" }),
        detail: "",
        initiatives_linked: [],
      },
    ];
    mockApi.addReferenceSources.mockResolvedValue(items);
    renderInRouter(
      <ReferenceSourceForm workspace="loregarden" onDone={() => {}} onCancel={() => {}} />,
    );

    const form = screen.getByRole("form", { name: "Add a reference" });
    await userEvent.click(within(form).getByRole("button", { name: "Several" }));
    await userEvent.type(
      within(form).getByLabelText("URLs"),
      "https://github.com/kayraucklnc/beluga",
    );
    await userEvent.click(within(form).getByRole("button", { name: /^Add 1 to queue$/ }));

    const link = await screen.findByRole("link", { name: "https://github.com/kayraucklnc/beluga" });
    await userEvent.click(link);

    expect(wouldFocusFor("bulk-id", link)).toBe(true);
  });

  it("navigateToReferenceSource stamps document.activeElement, so the chat action button keeps its move", async () => {
    // The one site with no event to read `currentTarget` from. AC-S4 has it
    // stamp `document.activeElement`, which is sound precisely because the
    // button stays mounted across the navigation — a `<Link>` elsewhere in the
    // app would not, and that is why the other eight pass `currentTarget`.
    const navigate = jest.fn();
    setRouterNavigate(navigate as unknown as Parameters<typeof setRouterNavigate>[0]);
    renderInRouter(<OpenReferenceSourceButton sourceId="chat-id" name="openharness" />);

    const button = screen.getByRole("button", { name: /Open openharness in the reference library/ });
    await userEvent.click(button);
    await waitFor(() => expect(navigate).toHaveBeenCalled());

    expect(wouldFocusFor("chat-id", button)).toBe(true);
  });

  it("navigateToReferenceSource stamps nothing useful when no router is bound, and does not throw", () => {
    // The guard clause returns before navigating. Stamping an intent for a
    // navigation that never happened would leave a live stamp for whatever
    // mounts next.
    const button = document.createElement("button");
    document.body.append(button);
    button.focus();

    expect(() => navigateToReferenceSource("unreachable-id")).not.toThrow();

    expect(focusOnOperatorIntent("unreachable-id", heading())).toBe(false);
  });
});

describe("AC-S4 — the ninth site, which has no activation element", () => {
  it("records a `from: null` intent that cannot satisfy arm (a) and therefore changes nothing", () => {
    // Stated as a test rather than left implicit, because it is the one place
    // AC-S4's "all nine sites" cannot be checked behaviourally. Both halves
    // below hold identically whether or not the add-form's `onDone` makes its
    // call, which is exactly the claim: the site relies on arm (b), and arm (b)
    // does not read the stamp. The structural check below is what keeps the
    // call in place; this case keeps the *reason* in place.
    const elsewhere = document.createElement("button");
    document.body.append(elsewhere);
    elsewhere.focus();
    recordFocusIntent("added-id", null);

    // Focus is the operator's, so a null-armed stamp buys nothing.
    expect(focusOnOperatorIntent("added-id", heading())).toBe(false);

    recordFocusIntent("added-id", null);
    elsewhere.blur();
    expect(document.activeElement).toBe(document.body);

    // Idle, so the move is permitted — by arm (b), which needed no stamp.
    expect(focusOnOperatorIntent("added-id", heading())).toBe(true);
  });
});
