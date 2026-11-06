import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api, ApiError } from "../../../api/client";
import type { TicketLandingStatus } from "../../../api/ticketLandingApi";
import { refusalReason } from "../../../lib/landingStatus";
import { TicketLandingPanel } from "../TicketLandingPanel";

jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return { ...actual, api: { ticketLanding: jest.fn() } };
});

const mockApi = api as jest.Mocked<typeof api>;

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function status(overrides: Partial<TicketLandingStatus> = {}): TicketLandingStatus {
  return {
    phase: "not_landing",
    target: "integration/lg-milestone-that-491",
    since: null,
    timeout_seconds: null,
    progress: "",
    progress_done: null,
    progress_total: null,
    ahead: [],
    ahead_count: 0,
    refused_attempts: 0,
    last_refusal: null,
    ...overrides,
  };
}

function renderPanel(answer: TicketLandingStatus | Error) {
  if (answer instanceof Error) mockApi.ticketLanding.mockRejectedValue(answer);
  else mockApi.ticketLanding.mockResolvedValue(answer);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketLandingPanel ticketId="t-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

it("says the landing tests are running, how far through, and against what limit", async () => {
  renderPanel(
    status({
      phase: "verifying",
      since: minutesAgo(12),
      timeout_seconds: 1800,
      progress: "pytest on 49 file(s) · 412/1830 (22%)",
      progress_done: 412,
      progress_total: 1830,
    }),
  );

  const panel = await screen.findByRole("region", { name: "What this landing is waiting on" });
  expect(panel).toHaveTextContent("Running the landing tests");
  expect(panel).toHaveTextContent("integration/lg-milestone-that-491");
  expect(panel).toHaveTextContent(/12m \d+s of a 30m limit/);
  expect(panel).toHaveTextContent("pytest on 49 file(s)");
  const bar = within(panel).getByRole("progressbar", { name: "Landing tests completed" });
  expect(bar).toHaveAttribute("value", "412");
  expect(bar).toHaveAttribute("max", "1830");
});

it("names who a waiting landing is behind, counts the rest, and links to the capacity board", async () => {
  renderPanel(
    status({
      phase: "waiting_for_capacity",
      since: minutesAgo(4),
      ahead: [
        { label: "gates land-verify abc · loregarden · lg-1", held: true, since: minutesAgo(9), progress: "" },
        { label: "pre-push · loregarden@main", held: false, since: minutesAgo(2), progress: "" },
      ],
      ahead_count: 5,
    }),
  );

  const panel = await screen.findByRole("region", { name: "What this landing is waiting on" });
  expect(panel).toHaveTextContent("Waiting for machine capacity");
  expect(panel).toHaveTextContent("Behind 5 jobs");
  expect(within(panel).getAllByRole("listitem")).toHaveLength(2);
  expect(panel).toHaveTextContent("and 3 more");
  expect(within(panel).getByRole("link", { name: "Open the capacity board" })).toHaveAttribute("href", "/machine");
});

it("says how many attempts were refused and why the last one was, with a way to its output", async () => {
  renderPanel(
    status({
      phase: "verifying",
      since: minutesAgo(1),
      timeout_seconds: 1800,
      refused_attempts: 7,
      last_refusal: {
        at: minutesAgo(3),
        outcome: "unavailable",
        command: "bash .lefthook/scripts/server-tests.sh",
        exit_code: null,
        artifact_id: "a-1",
      },
    }),
  );

  const panel = await screen.findByRole("region", { name: "What this landing is waiting on" });
  expect(panel).toHaveTextContent("The last 7 attempts were refused");
  expect(panel).toHaveTextContent("the tests never ran");
  expect(within(panel).getByRole("link", { name: "See the verification output" })).toHaveAttribute(
    "href",
    "/tickets/t-1/outputs",
  );
});

it("keeps a failing suite apart from tests that never ran", () => {
  const base = { at: null, command: "x", artifact_id: "a" };
  expect(refusalReason({ ...base, outcome: "failed", exit_code: 1 })).toContain("failed (exit 1)");
  expect(refusalReason({ ...base, outcome: "unavailable", exit_code: null })).toContain("never ran");
  expect(refusalReason({ ...base, outcome: "unavailable", exit_code: 69 })).toContain("could not run");
});

it("says the status could not be read, naming the failure, instead of going blank", async () => {
  renderPanel(new ApiError(503, "database is locked"));

  const message = await screen.findByRole("status");
  expect(message).toHaveTextContent("Could not read what this landing is waiting on");
  expect(message).toHaveTextContent("database is locked");
});

it("renders nothing when nothing is landing and nothing was refused", async () => {
  renderPanel(status());

  await waitFor(() => expect(mockApi.ticketLanding).toHaveBeenCalled());
  expect(screen.queryByRole("region", { name: "What this landing is waiting on" })).not.toBeInTheDocument();
});

it("still names the refusals on a blocked landing nothing is retrying", async () => {
  renderPanel(
    status({
      refused_attempts: 1,
      last_refusal: { at: minutesAgo(30), outcome: "failed", command: "x", exit_code: 1, artifact_id: "a" },
    }),
  );

  const panel = await screen.findByRole("region", { name: "What this landing is waiting on" });
  expect(panel).toHaveTextContent("The last attempt was refused: the tests failed (exit 1)");
  expect(panel).not.toHaveTextContent("Running the landing tests");
});

it("keeps the last answer on screen when a re-read fails, and says it may be stale", async () => {
  mockApi.ticketLanding.mockResolvedValueOnce(status({ phase: "verifying", since: minutesAgo(5), timeout_seconds: 1800 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketLandingPanel ticketId="t-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const panel = await screen.findByRole("region", { name: "What this landing is waiting on" });

  mockApi.ticketLanding.mockRejectedValueOnce(new ApiError(503, "database is locked"));
  await act(() => client.refetchQueries({ queryKey: ["ticket-landing", "t-1"] }));

  expect(panel).toHaveTextContent("Running the landing tests");
  expect(await screen.findByTestId("landing-refresh-failed")).toHaveTextContent("database is locked");
});
