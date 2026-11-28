import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import { createQueryClient } from "../../../api/queryClient";
import type { TicketArtifactItem } from "../../../api/types";
import { openOutput } from "../../../lib/ticketOutputs";
import { useReaderStore } from "../../../state/readerStore";
import { useToastStore } from "../../../state/toastStore";
import { Button } from "../../ui/Button";
import { ReaderHost } from "../ReaderHost";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

/**
 * The reader filling in over the network (lg-milestone-that-1424 AC6–AC10).
 *
 * The Outputs feed is bounded to 16 KiB a row, so a row the operator opens may
 * hold only the head of its log. The reader opens on that head immediately —
 * there is no blank loading state to design, because the prefix is where a
 * reader starts — and the rest arrives behind it. What these tests hold down is
 * the three ways that goes wrong: a prefix passed off as the whole output, a
 * failure that leaves the pane looking complete, and a late response landing in
 * a reader that has moved on.
 *
 * The in-flight status line is `role="status"` and the failed one `role="alert"`
 * — polite while it is only news, assertive once it is a failure the operator
 * has to act on, matching the `ap-state` error block in `TicketOutputs`.
 */

const PREFIX = "FAILED test_a.py::case — AssertionError\n".repeat(200);
const WHOLE = "FAILED test_a.py::case — AssertionError\n".repeat(26_000);
const SHOWN_BYTES = 16_384;
const TOTAL_BYTES = 1_048_576;

function truncated(overrides: Partial<TicketArtifactItem> = {}): TicketArtifactItem {
  return {
    id: "a1",
    kind: "land_verification",
    title: "Land verification — failed — loregarden/lg-r-1",
    run_id: null,
    stage_key: "done",
    system: false,
    evidence_kind: "",
    commit_sha: "",
    created_at: "2026-10-23T10:05:00",
    content_bytes: TOTAL_BYTES,
    content: { status: "failed", stage_key: "done", exit_code: 1, output: PREFIX },
    content_truncated: true,
    content_shown_bytes: SHOWN_BYTES,
    ...overrides,
  };
}

/** The same row as the detail route returns it: whole, and marked nowhere. */
function whole(item: TicketArtifactItem, output = WHOLE): TicketArtifactItem {
  const { content_truncated: _truncated, content_shown_bytes: _shown, ...rest } = item;
  return { ...rest, content: { status: "failed", stage_key: "done", exit_code: 1, output } };
}

function pending(): Promise<TicketArtifactItem> {
  return new Promise<TicketArtifactItem>(() => {});
}

function deferred(): { promise: Promise<TicketArtifactItem>; resolve: (item: TicketArtifactItem) => void } {
  let resolve!: (item: TicketArtifactItem) => void;
  const promise = new Promise<TicketArtifactItem>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/** The row's title button, which stays the only control that opens a row (AC11). */
function Row({ item }: { item: TicketArtifactItem }) {
  return (
    <Button variant="plain" onClick={() => openOutput(item, { ticketId: "t1" })}>
      open {item.id}
    </Button>
  );
}

/**
 * The app's own client, not a bare `new QueryClient`: `createQueryClient` hangs
 * a `QueryCache.onError` that toasts any query failing with no data, and AC8
 * requires this failure to be reported inline instead. A harness without it
 * passes the "no toast" assertions below for the wrong reason. (implement)
 */
function renderReader(items: TicketArtifactItem[] = [truncated()]) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
      <ReaderHost />
    </QueryClientProvider>,
  );
}

const loadingLine = () => screen.queryByRole("status");
const errorLine = () => screen.queryByRole("alert");
const dialog = () => screen.getByRole("dialog");
const openRow = (id: string) => fireEvent.click(screen.getByRole("button", { name: `open ${id}` }));

beforeEach(() => {
  jest.clearAllMocks();
  act(() => useReaderStore.getState().close());
  useToastStore.setState({ toasts: [] });
});

afterEach(() => act(() => useReaderStore.getState().close()));

// --- AC6: opening stays synchronous ----------------------------------------

it("opens the reader without awaiting anything, carrying the artifact's id and its partial", () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  const item = truncated();

  // No `await`, no act flush: the store must already hold the document by the
  // time `openOutput` returns, or the reader opens a frame late on every row.
  openOutput(item, { ticketId: "t1" });

  const opened = useReaderStore.getState().document;
  expect(opened?.id).toBe("a1");
  expect(opened?.partial).toEqual({
    ticketId: "t1",
    artifactId: "a1",
    shownBytes: SHOWN_BYTES,
    totalBytes: TOTAL_BYTES,
  });
});

it("carries no partial for a row that was not shortened", () => {
  openOutput(whole(truncated(), PREFIX), { ticketId: "t1" });

  expect(useReaderStore.getState().document?.partial).toBeUndefined();
  expect(useReaderStore.getState().document?.id).toBe("a1");
});

it("places the row by its FULL size, not by the part it is showing", () => {
  openOutput(truncated(), { ticketId: "t1", stage: "done" });

  // `content_bytes` is the whole stored size, so the subtitle stays truthful
  // about what the operator is about to read (AC4).
  const subtitle = useReaderStore.getState().document?.subtitle ?? "";
  expect(subtitle).toContain("1.0 MB");
  // And the second argument is read as options, not as the bare `stage` string
  // it used to be: `[stage || item.stage_key]` with an object in it is truthy
  // and joins as "[object Object]", which left this assertion green against
  // the signature AC6 replaces. (test-break)
  expect(subtitle).not.toContain("[object Object]");
  expect(subtitle).toMatch(/^land_verification · done · /);
});

// --- AC7: loading ----------------------------------------------------------

it("shows the prefix at once, with one status line and no spinner or skeleton", async () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  renderReader();

  openRow("a1");

  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");
  expect(loadingLine()).toHaveTextContent("Showing the first 16.0 KB — loading all 1.0 MB…");
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(dialog().querySelector(".pane-skeleton")).toBeNull();
  await waitFor(() => expect(mockApi.ticketArtifact).toHaveBeenCalledWith("t1", "a1"));
});

it("keeps the prefix readable while the rest loads, then grows into the whole of it", async () => {
  const rest = deferred();
  mockApi.ticketArtifact.mockReturnValue(rest.promise);
  const item = truncated();
  renderReader([item]);

  openRow("a1");
  // The body is not replaced by a loading state: the head of the log is there.
  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");

  await act(async () => rest.resolve(whole(item)));

  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());
  expect(useReaderStore.getState().document?.content).toEqual(whole(item).content);
});

it("keeps the scroll container mounted across the fill-in, so the offset survives", async () => {
  const rest = deferred();
  mockApi.ticketArtifact.mockReturnValue(rest.promise);
  const item = truncated();
  renderReader([item]);
  openRow("a1");

  const body = dialog().querySelector(".reader-body");
  const measure = dialog().querySelector(".reader-measure");
  await act(async () => rest.resolve(whole(item)));
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());

  // Same nodes, not replacements: a container keyed on `document` or on
  // `content` remounts, and the operator's place in a 1 MB log is lost.
  expect(dialog().querySelector(".reader-body")).toBe(body);
  expect(dialog().querySelector(".reader-measure")).toBe(measure);
});

it("says it is showing a prefix in Source mode too", () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  renderReader();
  openRow("a1");

  fireEvent.click(screen.getByRole("button", { name: "Source" }));

  // Source must never present the prefix unlabelled as the source.
  expect(loadingLine()).toHaveTextContent("Showing the first 16.0 KB — loading all 1.0 MB…");
});

it("has no status line for a document that is already whole", () => {
  renderReader([whole(truncated(), PREFIX)]);

  openRow("a1");

  expect(loadingLine()).not.toBeInTheDocument();
  expect(errorLine()).not.toBeInTheDocument();
  expect(mockApi.ticketArtifact).not.toHaveBeenCalled();
});

// --- AC8: error ------------------------------------------------------------

it("reports a failed fill-in inline, keeps the prefix, and offers Try again", async () => {
  mockApi.ticketArtifact.mockRejectedValue(new Error("artifact read failed"));
  renderReader();

  openRow("a1");

  const failure = await screen.findByRole("alert");
  expect(failure).toHaveTextContent("Could not load the rest of this output.");
  expect(failure).toHaveTextContent("artifact read failed");
  // The prefix stays on screen: a failure must not empty the pane.
  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  // Reported where the operator is looking, not through a toast owned by
  // `ToastHost` in `AppLayout` — the reader is a modal over an overlay.
  expect(useToastStore.getState().toasts).toHaveLength(0);
});

it("still says the document is partial after a failure", async () => {
  mockApi.ticketArtifact.mockRejectedValue(new Error("artifact read failed"));
  renderReader();
  openRow("a1");

  await screen.findByRole("button", { name: "Try again" });

  // The failed state must never leave the reader looking complete.
  expect(useReaderStore.getState().document?.partial).not.toBeUndefined();
});

it("retries the same query rather than starting a new one", async () => {
  const item = truncated();
  mockApi.ticketArtifact.mockRejectedValueOnce(new Error("artifact read failed"));
  mockApi.ticketArtifact.mockResolvedValueOnce(whole(item));
  renderReader([item]);
  openRow("a1");
  const retry = await screen.findByRole("button", { name: "Try again" });

  fireEvent.click(retry);

  await waitFor(() => expect(errorLine()).not.toBeInTheDocument());
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());
  expect(mockApi.ticketArtifact).toHaveBeenCalledTimes(2);
  expect(useReaderStore.getState().document?.content).toEqual(whole(item).content);
});

// --- AC9: double-fire, and no prefix passed off as whole --------------------

it("serves a second click on the same row from the cache, with one request", async () => {
  const item = truncated();
  mockApi.ticketArtifact.mockResolvedValue(whole(item));
  renderReader([item]);

  openRow("a1");
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());
  act(() => useReaderStore.getState().close());
  openRow("a1");
  await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

  expect(mockApi.ticketArtifact).toHaveBeenCalledTimes(1);
});

it("refuses to copy a prefix as if it were the whole output", () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  renderReader();

  openRow("a1");

  const copy = screen.getByRole("button", { name: /^Copy/ });
  expect(copy).toHaveAttribute("aria-disabled", "true");
  // An accessible name that says WHY, not a bare disabled control.
  expect(copy.textContent ?? "").not.toBe("Copy");
  expect((copy.getAttribute("aria-label") ?? copy.textContent ?? "").length).toBeGreaterThan(
    "Copy".length,
  );
});

it("names Copy for the prefix once the fill-in has failed", async () => {
  mockApi.ticketArtifact.mockRejectedValue(new Error("artifact read failed"));
  renderReader();
  openRow("a1");

  await screen.findByRole("button", { name: "Try again" });

  const copy = screen.getByRole("button", { name: "Copy first 16.0 KB" });
  expect(copy).not.toHaveAttribute("aria-disabled", "true");
});

it("is the plain Copy once the whole output has landed", async () => {
  const item = truncated();
  mockApi.ticketArtifact.mockResolvedValue(whole(item));
  renderReader([item]);
  openRow("a1");

  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());

  const copy = screen.getByRole("button", { name: "Copy" });
  expect(copy).not.toHaveAttribute("aria-disabled", "true");
});

// --- AC10: keyboard, focus, and a late response ----------------------------

it("keeps Source selected when the rest arrives", async () => {
  const rest = deferred();
  mockApi.ticketArtifact.mockReturnValue(rest.promise);
  const item = truncated();
  renderReader([item]);
  openRow("a1");
  fireEvent.click(screen.getByRole("button", { name: "Source" }));

  await act(async () => rest.resolve(whole(item)));
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());

  // The effect that resets `raw` is keyed on the document's id, not its object
  // identity: replacing the object would throw an operator mid-read back to
  // Formatted.
  expect(screen.getByRole("button", { name: "Formatted" })).toHaveAttribute("aria-pressed", "true");
});

it("does not move focus when the rest arrives", async () => {
  const rest = deferred();
  mockApi.ticketArtifact.mockReturnValue(rest.promise);
  const item = truncated();
  renderReader([item]);
  openRow("a1");
  const source = screen.getByRole("button", { name: "Source" });
  act(() => source.focus());

  await act(async () => rest.resolve(whole(item)));
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());

  expect(document.activeElement).toBe(source);
});

it("closes on Escape and leaves a late response nowhere to land", async () => {
  const rest = deferred();
  mockApi.ticketArtifact.mockReturnValue(rest.promise);
  const item = truncated();
  renderReader([item]);
  openRow("a1");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await act(async () => rest.resolve(whole(item)));

  // A resolved fetch must not reopen a reader the operator closed.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(useReaderStore.getState().document).toBeNull();
});

it("discards a response for a row the operator has already navigated away from", async () => {
  const first = truncated({ id: "a1" });
  const second = truncated({
    id: "a2",
    title: "Land verification — passed — loregarden/lg-r-2",
    content: { status: "passed", stage_key: "done", exit_code: 0, output: "42 passed\n" },
  });
  const firstRest = deferred();
  mockApi.ticketArtifact.mockImplementation((_ticketId: string, artifactId: string) =>
    artifactId === "a1" ? firstRest.promise : Promise.resolve(whole(second, "42 passed\n")),
  );
  renderReader([first, second]);

  openRow("a1");
  openRow("a2");
  await waitFor(() => expect(useReaderStore.getState().document?.id).toBe("a2"));
  await act(async () => firstRest.resolve(whole(first)));

  // `completeDocument` is a no-op unless the id it names is still the open one.
  expect(useReaderStore.getState().document?.id).toBe("a2");
  expect(dialog()).toHaveTextContent("42 passed");
  expect(dialog()).not.toHaveTextContent("FAILED test_a.py::case");
});

it("ignores completeDocument for an id that is not the open document", () => {
  openOutput(truncated(), { ticketId: "t1" });
  const before = useReaderStore.getState().document;

  act(() => useReaderStore.getState().completeDocument("some-other-id", { output: WHOLE }));

  expect(useReaderStore.getState().document).toBe(before);
});

it("ignores completeDocument when nothing is open", () => {
  act(() => useReaderStore.getState().completeDocument("a1", { output: WHOLE }));

  expect(useReaderStore.getState().document).toBeNull();
});

it("leaves a plain markdown document openable with no partial and no fetch", () => {
  renderReader([]);

  act(() => useReaderStore.getState().open({ title: "Attention", content: "A teaser." }));

  // `TicketAttention.tsx` hands the reader a markdown document and no `partial`.
  expect(screen.getByRole("dialog", { name: "Attention" })).toHaveTextContent("A teaser.");
  expect(loadingLine()).not.toBeInTheDocument();
  expect(errorLine()).not.toBeInTheDocument();
  expect(mockApi.ticketArtifact).not.toHaveBeenCalled();
});

// ===========================================================================
// Adversarial cases (test-break stage).
//
// Everything above holds the happy path and the three stated failure modes.
// These are the ways the reader can pass all of it and still hand an operator a
// prefix as the whole output: `aria-disabled` is not `disabled`, so the
// in-flight Copy is still clickable; the Copy that lands after the fill-in has
// to carry the *new* content and not the string it closed over; and a server
// that answers with a shortened row again must not become a request loop.
// ===========================================================================

const TAIL = "LAST LINE OF THE WHOLE LOG\n";
const WHOLE_WITH_TAIL = PREFIX + "middle\n".repeat(1_000) + TAIL;

let written: string[] = [];

beforeEach(() => {
  written = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: (text: string) => { written.push(text); return Promise.resolve(); } },
  });
});

it("writes nothing to the clipboard when the in-flight Copy is clicked anyway", () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  renderReader();
  openRow("a1");

  fireEvent.click(screen.getByRole("button", { name: /^Copy/ }));

  // `aria-disabled="true"` on a real <button> does not stop a click, and the
  // row's own tests only read the attribute. If the handler is still wired, the
  // operator pastes 16 KB of a 1 MB log into a bug report believing it whole —
  // which is the exact thing AC9 says must never happen.
  expect(written).toEqual([]);
  expect(screen.queryByRole("button", { name: "Copied" })).not.toBeInTheDocument();
});

it("copies the whole output once it has landed, not the prefix it opened on", async () => {
  const item = truncated();
  mockApi.ticketArtifact.mockResolvedValue(whole(item, WHOLE_WITH_TAIL));
  renderReader([item]);
  openRow("a1");
  await waitFor(() => expect(loadingLine()).not.toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Copy" }));

  await waitFor(() => expect(written).toHaveLength(1));
  // Kills a `source` computed once at open, or memoised on the document object
  // the fill-in replaced: the button would read "Copy" and copy the prefix.
  //
  // Asserted without `TAIL`'s trailing newline: this document's `content` is an
  // object, so the reader's source — `JSON.stringify(content, null, 2)`, as it
  // has been for every artifact since the reader existed — escapes newlines to
  // `\n`. A literal `TAIL` could only appear if `Copy` handed over the raw
  // `output` string instead of the document, which no criterion asks for (the
  // reader's layout of a multi-line `output` is sibling lg-milestone-that-1425).
  // The line itself is what distinguishes the whole from the prefix, so the
  // mutants this case was written to kill still die on it. (implement)
  expect(written[0]).toContain("LAST LINE OF THE WHOLE LOG");
});

it("copies only what its name promises after a failed fill-in", async () => {
  mockApi.ticketArtifact.mockRejectedValue(new Error("artifact read failed"));
  renderReader();
  openRow("a1");
  await screen.findByRole("button", { name: "Copy first 16.0 KB" });

  fireEvent.click(screen.getByRole("button", { name: "Copy first 16.0 KB" }));

  await waitFor(() => expect(written).toHaveLength(1));
  expect(written[0]).not.toContain(TAIL);
  expect(written[0]).toContain("FAILED test_a.py::case");
});

it("issues one request for two clicks on the same row with the reader still open", async () => {
  const item = truncated();
  mockApi.ticketArtifact.mockReturnValue(pending());
  renderReader([item]);

  openRow("a1");
  openRow("a1");
  await waitFor(() => expect(mockApi.ticketArtifact).toHaveBeenCalled());

  // The double-fire AC9 names is the impatient second click, which arrives
  // before the first read returns and without the reader being closed.
  expect(mockApi.ticketArtifact).toHaveBeenCalledTimes(1);
});

it("does not re-request when the answer itself comes back marked as partial", async () => {
  const item = truncated();
  // A server that bounded the detail route too, or a stale cache entry: the
  // reader must stop, not chase the rest forever on a 1 MB row.
  mockApi.ticketArtifact.mockResolvedValue(truncated());
  renderReader([item]);

  openRow("a1");
  await waitFor(() => expect(mockApi.ticketArtifact).toHaveBeenCalled());

  expect(mockApi.ticketArtifact).toHaveBeenCalledTimes(1);
  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");
});

it("never shows the operator an undefined or NaN size", () => {
  mockApi.ticketArtifact.mockReturnValue(pending());
  // `content_shown_bytes` is optional on the wire, so a row can arrive marked
  // and unmeasured — an older server, or a cached response. The status line is
  // the operator's only account of what they are looking at; "Showing the first
  // undefined" is worse than not saying it.
  renderReader([truncated({ content_shown_bytes: undefined })]);

  openRow("a1");

  const spoken = dialog().textContent ?? "";
  expect(spoken).not.toMatch(/undefined|NaN/);
  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");
});

it("keeps the prefix and the way back when Try again fails too", async () => {
  mockApi.ticketArtifact.mockRejectedValue(new Error("artifact read failed"));
  renderReader();
  openRow("a1");
  fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

  await waitFor(() => expect(mockApi.ticketArtifact).toHaveBeenCalledTimes(2));

  // A second failure must not empty the pane or retire the control: the
  // operator's next move is still the same one.
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the rest of this output.");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(dialog()).toHaveTextContent("FAILED test_a.py::case");
  expect(useToastStore.getState().toasts).toHaveLength(0);
});
