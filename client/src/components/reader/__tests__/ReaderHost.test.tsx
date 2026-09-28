import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useReaderStore } from "../../../state/readerStore";
import { MarkdownContent } from "../../chat/MarkdownContent";
import { ReaderHost } from "../ReaderHost";

afterEach(() => act(() => useReaderStore.getState().close()));

const LONG = Array.from({ length: 20 }, (_, i) => `Paragraph ${i} of the review.`).join("\n\n");

it("offers no Open control on short markdown", () => {
  render(<MarkdownContent content="A short reply." />);

  expect(screen.queryByRole("button", { name: /in the reader/ })).not.toBeInTheDocument();
});

it("opens long markdown in the reader, titled, and closes on Escape", async () => {
  const user = userEvent.setup();
  render(
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
  render(<ReaderHost />);
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
