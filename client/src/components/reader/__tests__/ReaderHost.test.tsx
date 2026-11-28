import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useReaderStore } from "../../../state/readerStore";
import { MarkdownContent } from "../../chat/MarkdownContent";
import { ReaderHost } from "../ReaderHost";

afterEach(() => act(() => useReaderStore.getState().close()));

/**
 * The reader fetches the rest of a shortened document (lg-milestone-that-1424
 * AC6-AC10), so it needs a query client the way it needs the store. In the app
 * it is mounted inside `App`'s provider; here every render supplies its own.
 */
function renderInApp(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const LONG = Array.from({ length: 20 }, (_, i) => `Paragraph ${i} of the review.`).join("\n\n");

it("offers no Open control on short markdown", () => {
  render(<MarkdownContent content="A short reply." />);

  expect(screen.queryByRole("button", { name: /in the reader/ })).not.toBeInTheDocument();
});

it("opens long markdown in the reader, titled, and closes on Escape", async () => {
  const user = userEvent.setup();
  renderInApp(
    <>
      <MarkdownContent content={LONG} readerTitle="Security review" />
      <ReaderHost />
    </>,
  );

  await user.click(screen.getByRole("button", { name: "Open Security review in the reader" }));

  const dialog = screen.getByRole("dialog", { name: "Security review" });
  expect(dialog).toHaveTextContent("Paragraph 19 of the review.");

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("lays an object out as facts and sections, with the raw source a toggle away", () => {
  renderInApp(<ReaderHost />);
  act(() =>
    useReaderStore.getState().open({
      title: "Review",
      content: { verdict: "needs_rework", root_cause: "The gate ran on nothing.\n\nIt exited 0 with no files staged." },
    }),
  );

  expect(screen.getByText("Verdict")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Root cause" })).toBeInTheDocument();
  expect(screen.queryByText(/"verdict":/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Source" }));

  expect(screen.getByText(/"verdict": "needs_rework"/)).toBeInTheDocument();
});

it("shows a stage report's verdict once, beside the title, and each fact once", () => {
  renderInApp(<ReaderHost />);
  act(() =>
    useReaderStore.getState().open({
      title: "Stage report — gate",
      badge: { text: "pass · 0.91", tone: "good" },
      content: {
        stage_key: "gate",
        status: "pass",
        confidence: 0.91,
        reroute_to_stage: null,
        rows: [
          { k: "status", v: "pass" },
          { k: "confidence", v: "0.91" },
          { k: "reroute_to_stage", v: "—" },
        ],
      },
    }),
  );

  expect(screen.getByRole("heading", { name: /Stage report — gate pass · 0.91/ })).toBeInTheDocument();
  expect(screen.getAllByText("Status")).toHaveLength(1);
  expect(screen.queryByRole("heading", { name: "Rows" })).not.toBeInTheDocument();
  expect(screen.queryByText("Reroute to stage")).not.toBeInTheDocument();
});

it("lays a handoff's checklist out as a table, without a heading for its wrapper", () => {
  renderInApp(<ReaderHost />);
  act(() =>
    useReaderStore.getState().open({
      title: "handoff spec → test_designer",
      content: {
        handoff: {
          from_agent: "spec",
          checklist: [
            { item_key: "ac_met", required: true, status: "complete", evidence: "40 passed" },
            { item_key: "spec_matches", required: true, status: "missing", evidence: "" },
          ],
        },
      },
    }),
  );

  expect(screen.queryByRole("heading", { name: "Handoff" })).not.toBeInTheDocument();
  const table = screen.getByRole("table");
  expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
    "Item key",
    "Required",
    "Status",
    "Evidence",
  ]);
  expect(table).toHaveTextContent("missing");
});

it("renders a diff artifact's lines as a diff, not one card per line", () => {
  renderInApp(<ReaderHost />);
  act(() =>
    useReaderStore.getState().open({
      title: "client/src/api/types.ts",
      content: {
        lines: [
          { type: "h", ln: "", text: "@@ -1,2 +1,2 @@" },
          { type: "d", ln: "", text: "old line" },
          { type: "a", ln: "", text: "new line" },
        ],
      },
    }),
  );

  const added = screen.getByText("new line", { exact: false });
  expect(added).toHaveClass("sc-diff-line--a");
  expect(added).toHaveTextContent("+new line");
  expect(screen.getByText("old line", { exact: false })).toHaveTextContent("-old line");
  expect(screen.queryByText(/"type":/)).not.toBeInTheDocument();
});
