import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { AutoDecision } from "../../../api/autoDecisionApi";
import { api, ApiError } from "../../../api/client";
import { AutoDecisionsPanel } from "../AutoDecisionsPanel";

jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return { ...actual, api: { autoDecisions: jest.fn() } };
});

const mockApi = api as jest.Mocked<typeof api>;

function decision(overrides: Partial<AutoDecision> = {}): AutoDecision {
  return {
    ticket_id: "t-2",
    external_id: "lg-milestone-that-2300",
    title: "Width on every arm",
    decision: "split_criteria",
    reason: "1 criteria were not met within the rework budget and moved to lg-milestone-that-2301.",
    at: new Date().toISOString(),
    related_external_id: "lg-milestone-that-2301",
    ...overrides,
  };
}

function renderPanel(answer: AutoDecision[] | Error, alwaysShow: boolean) {
  if (answer instanceof Error) mockApi.autoDecisions.mockRejectedValue(answer);
  else mockApi.autoDecisions.mockResolvedValue(answer);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AutoDecisionsPanel ticketId="t-1" alwaysShow={alwaysShow} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

it("lists a split with links to the ticket it happened on and the ticket it made", async () => {
  renderPanel([decision()], false);
  const list = await screen.findByRole("list", { name: "Decided without you" });
  expect(within(list).getByRole("link", { name: "lg-milestone-that-2300" })).toBeInTheDocument();
  expect(within(list).getByRole("link", { name: "lg-milestone-that-2301" })).toBeInTheDocument();
});

it("on a tree root, says what would be listed when nothing has been decided", async () => {
  renderPanel([], true);
  expect(await screen.findByText(/Nothing yet\./)).toBeInTheDocument();
});

it("elsewhere, renders nothing when nothing has been decided", async () => {
  const answered = Promise.resolve([] as AutoDecision[]);
  mockApi.autoDecisions.mockReturnValue(answered);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AutoDecisionsPanel ticketId="t-1" alwaysShow={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await act(async () => {
    await answered;
  });
  await waitFor(() => expect(client.getQueryState(["auto-decisions", "t-1"])?.status).toBe("success"));
  expect(container).toBeEmptyDOMElement();
});

it("names the failure instead of showing an empty list", async () => {
  renderPanel(new ApiError(500, "boom"), true);
  expect(await screen.findByRole("status")).toHaveTextContent(/Could not load the decisions/);
});
