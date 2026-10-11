import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

import { api } from "../../../api/client";
import type { ChildrenTimeline, TicketDetail } from "../../../api/client";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import recorded from "../../../test/fixtures/prod-shape/parent-children-timeline.json";
import { ParentTimeline } from "../ParentTimeline";

jest.mock("../../../api/client");
jest.mock("../../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../../lib/useAppNavigation"),
  navigateToTicket: jest.fn(),
}));

const mockApi = api as jest.Mocked<typeof api>;

/** The API's own response over the prod-shape scenario's worked milestone (live p90). */
const milestone = recorded as unknown as ChildrenTimeline;

const parent = { id: milestone.parent_id, child_count: milestone.children.length } as unknown as TicketDetail;

function renderParent(own: ReactNode = <p>own runs</p>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ParentTimeline ticket={parent} isActive={false} own={own} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.ticketChildrenTimeline.mockResolvedValue(milestone);
});

describe("at production volume", () => {
  it("control: the children as a flat list with nothing to open is flagged", () => {
    const { container } = render(
      <ul>
        {milestone.children.map((c) => (
          <li key={c.id}>
            <span>{c.external_id}</span> <span>{c.title}</span> <span>{c.state}</span>{" "}
            <span>{c.workflow_stage_key}</span> <span>{c.workflow_stage_status}</span> <span>{c.total_runs}</span>
          </li>
        ))}
      </ul>,
    );
    expect(findUsabilityProblems(container).map((p) => p.kind)).toContain("dead-end-list");
  });

  it("the parent timeline is not", async () => {
    const { container } = renderParent();
    await screen.findByText("1 of 24 done");
    expect(findUsabilityProblems(container)).toEqual([]);
  });
});

it("leads with what needs a person, and filters to it", async () => {
  renderParent();
  const waiting = await screen.findByRole("region", { name: "Children waiting on you" });
  expect(within(waiting).getByText("11 child tickets waiting on you")).toBeInTheDocument();
  expect(within(waiting).getByText("11 at playtest")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Blocked children" })).toHaveTextContent("4 blocked");

  fireEvent.click(within(waiting).getByRole("button", { name: "Show 11" }));

  const lanes = screen.getByRole("region", { name: "Child tickets that have run" });
  // The eleven at one place collapse behind a count even when filtered to.
  fireEvent.click(within(lanes).getByRole("button", { name: /\+ 10 more at playtest · needs you/ }));
  expect(within(lanes).getAllByText(/· needs you/, { selector: ".ptl-now" })).toHaveLength(11);
  expect(screen.queryByRole("region", { name: "Child tickets that have not run" })).not.toBeInTheDocument();
});

it("collapses a run of children at the same place and expands it", async () => {
  renderParent();
  const lanes = await screen.findByRole("region", { name: "Child tickets that have run" });
  const more = within(lanes).getByRole("button", { name: "+ 10 more at playtest · needs you" });
  expect(more).toHaveAttribute("aria-expanded", "false");
  // done 1 + blocked 4 + first of the 11 + 2 in progress elsewhere.
  expect(within(lanes).getAllByRole("button", { name: /^blob-m90-/ })).toHaveLength(8);

  fireEvent.click(more);

  expect(within(lanes).getAllByRole("button", { name: /^blob-m90-/ })).toHaveLength(18);
  expect(within(lanes).getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
});

it("splits the not-started children into ready and waiting, naming what they wait on", async () => {
  renderParent();
  const pending = await screen.findByRole("region", { name: "Child tickets that have not run" });
  expect(within(pending).getByRole("button", { name: /Ready to start · 4/ })).toBeInTheDocument();
  expect(within(pending).getByRole("button", { name: /Waiting on other tickets · 2/ })).toBeInTheDocument();

  const dep = within(pending).getByRole("button", { name: "blob-m90-97" });
  fireEvent.click(dep);
  expect(navigateToTicket).toHaveBeenCalledWith(milestone.children.find((c) => c.external_id === "blob-m90-97")!.id);
});

it("opens a child from its name", async () => {
  renderParent();
  const lanes = await screen.findByRole("region", { name: "Child tickets that have run" });
  fireEvent.click(within(lanes).getByRole("button", { name: /^blob-m90-92/ }));
  expect(navigateToTicket).toHaveBeenCalledWith(milestone.children[1].id);
});

it("gives each bar a summary a screen reader can read", async () => {
  renderParent();
  await screen.findByText("1 of 24 done");
  expect(screen.getByText(/^12 stage visits, 14 runs, went back 2 times, now Done$/)).toBeInTheDocument();
});

it("expands a child with children of its own, on the same axis", async () => {
  const [first, ...rest] = milestone.children;
  const nested: ChildrenTimeline = { ...milestone, parent_id: first.id, children: rest.slice(0, 2) };
  mockApi.ticketChildrenTimeline.mockImplementation(async (id) =>
    id === first.id ? nested : { ...milestone, children: [{ ...first, child_count: 2 }, ...rest] },
  );
  renderParent();

  const toggle = await screen.findByRole("button", { name: "Expand blob-m90-91, 2 child tickets" });
  fireEvent.click(toggle);

  expect(await screen.findByRole("button", { name: "Collapse blob-m90-91, 2 child tickets" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(mockApi.ticketChildrenTimeline).toHaveBeenCalledWith(first.id);
});

it("says so when there are no children", async () => {
  mockApi.ticketChildrenTimeline.mockResolvedValue({ ...milestone, children: [], window_start: null, window_end: null });
  renderParent();
  expect(await screen.findByText("No child tickets yet")).toBeInTheDocument();
});

it("names the failure and offers a retry", async () => {
  mockApi.ticketChildrenTimeline.mockRejectedValueOnce(new Error("boom"));
  renderParent();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("boom");
  mockApi.ticketChildrenTimeline.mockResolvedValue(milestone);
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("1 of 24 done")).toBeInTheDocument();
});

it("keeps the parent's own runs behind a switch", async () => {
  renderParent(<p>the parent&apos;s own ledger</p>);
  await screen.findByText("1 of 24 done");
  const own = screen.getByRole("button", { name: "This ticket’s own runs" });
  expect(own).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(own);
  expect(screen.getByText("the parent's own ledger")).toBeInTheDocument();
  expect(screen.queryByText("1 of 24 done")).not.toBeInTheDocument();
});
