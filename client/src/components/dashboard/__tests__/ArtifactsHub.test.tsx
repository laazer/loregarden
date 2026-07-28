import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { TicketDetail } from "../../../api/client";
import { api } from "../../../api/client";
import { ArtifactsHub } from "../ArtifactsHub";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function makeTicket(): TicketDetail {
  return {
    id: "t1",
    stages: [],
    blocking_issues: "",
    artifacts: {},
  } as unknown as TicketDetail;
}

function renderHub(subTab: "artifacts" | "errors" | "context" | "ledger" = "artifacts") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ArtifactsHub ticket={makeTicket()} subTab={subTab} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.ticketArtifacts.mockResolvedValue({ total: 0, items: [] });
  mockApi.ticketLedger.mockResolvedValue({
    visits: [],
    total_runs: 0,
    reworked_stages: [],
    total_seconds: 0,
  });
});

it("renders Feed / Errors / Context / Ledger sub-tabs", async () => {
  renderHub();
  expect(await screen.findByRole("tab", { name: "Feed" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Errors" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Context" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Ledger" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Feed" })).toHaveAttribute("aria-selected", "true");
});

it("marks the Errors sub-tab when selected", async () => {
  renderHub("errors");
  expect(await screen.findByRole("tab", { name: "Errors" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("No errors recorded")).toBeInTheDocument();
});

it("shows the ledger empty state under the Ledger sub-tab", async () => {
  renderHub("ledger");
  expect(await screen.findByRole("tab", { name: "Ledger" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText(/nothing has run for this ticket yet/i)).toBeInTheDocument();
});
