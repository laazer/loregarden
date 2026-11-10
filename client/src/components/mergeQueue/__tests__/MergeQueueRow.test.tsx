/**
 * `.mq-log` is a bounded `overflow: auto` <pre> holding the failing log tail of
 * a stopped entry (lg-milestone-that-2215). The `<summary>` that opens it and
 * the `Retry` button both sit OUTSIDE it, so once the tail was longer than the
 * box nothing could scroll it without a mouse.
 *
 * The region lived on the workspace dashboard's own `QueueRow` until the row
 * was extracted here so Home, Lanes and the ticket strip could render the same
 * entry; its keyboard tests moved with it.
 */
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mergeQueueApi, type MergeQueueEntry, type MergeQueueView } from "../../../api/mergeQueueApi";
import queueFixture from "../../../test/fixtures/prod-shape/merge-queue-loregarden.json";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { MergeQueueRow } from "../MergeQueueRow";

jest.mock("../../../api/mergeQueueApi", () => ({
  ...jest.requireActual("../../../api/mergeQueueApi"),
  mergeQueueApi: { queue: jest.fn(), entry: jest.fn(), retry: jest.fn(), withdraw: jest.fn() },
}));

/** The API's own response over the prod-shape scenario. */
const prodShape = queueFixture as unknown as MergeQueueView;
const NOW = new Date("2026-09-28T00:10:00Z");

function stopped(): MergeQueueEntry {
  const found = prodShape.entries.find((entry) => Boolean(entry.log_tail) && entry.can_retry);
  if (!found) throw new Error("the fixture has no retryable stopped entry with a log tail");
  return found;
}

function render(entry: MergeQueueEntry) {
  return renderWithRouter(
    <ul>
      <MergeQueueRow entry={entry} now={NOW} busy={undefined} onAct={jest.fn()} />
    </ul>,
  );
}

beforeEach(() => {
  jest.resetAllMocks();
  (mergeQueueApi.entry as jest.Mock).mockResolvedValue({ ...stopped(), checks: [], checks_live: true });
});

describe("the failing log tail's keyboard path", () => {
  const REGION = "Failing log tail";

  it("is a tab stop that steps back to that entry's Retry — AC3", async () => {
    const entry = stopped();
    render(entry);

    // The region is behind a <details>, so the test opens it first.
    fireEvent.click(await screen.findByText(REGION));

    const pane = screen.getByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // The nearest tab stop before the pane is that entry's own `Retry`, which
    // is the action its log tail leads to. The <summary> above it is not one:
    // a <summary> takes focus in a browser but carries no `tabIndex`, so it is
    // not where the keyboard arrives from here.
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(
      screen.getByRole("button", { name: `Retry PR #${entry.pr_number} in the merge queue` }),
    ).toHaveFocus();
  });

  it("renders no labelled pane for an entry that logged nothing — AC8", async () => {
    const entry = stopped();
    render({ ...entry, log_tail: "" });

    // An empty `log_tail` is an entry that stopped without logging, and the
    // reason line above still says why. A named box over nothing is the dead
    // tab stop AC8 forbids.
    await screen.findByText(/required check failed on the staged commit/);
    expect(screen.queryByText(REGION)).toBeNull();
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });
});
