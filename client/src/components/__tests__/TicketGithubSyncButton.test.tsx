import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { githubIssueApi, type GithubLinkSyncResult } from "../../api/githubIssueApi";
import { ApiError } from "../../api/http";
import { useToastStore } from "../../state/toastStore";
import { TicketGithubSyncButton } from "../TicketGithubSyncButton";

jest.mock("../../api/githubIssueApi", () => ({
  githubIssueApi: { link: jest.fn(), sync: jest.fn() },
}));

const mockLink = githubIssueApi.link as jest.Mock;
const mockSync = githubIssueApi.sync as jest.Mock;
const TICKET = "11111111-1111-4111-8111-111111111111";

const LINKED = {
  ticket_id: TICKET,
  repo: "acme/widgets",
  issue_number: 42,
  issue_url: "https://github.com/acme/widgets/issues/42",
  last_synced_at: "2026-10-26T10:00:00Z",
  last_error: "",
};

function result(overrides: Partial<GithubLinkSyncResult> = {}): GithubLinkSyncResult {
  return {
    ticket_id: TICKET,
    external_id: "lg-auth-7",
    issue_number: 42,
    issue_url: LINKED.issue_url,
    pushed: [],
    pulled: [],
    conflicts: [],
    error: "",
    ...overrides,
  };
}

function renderButton() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TicketGithubSyncButton ticketId={TICKET} />
    </QueryClientProvider>,
  );
}

const toastTitles = () => useToastStore.getState().toasts.map((t) => `${t.tone}:${t.title}`);

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

test("an unlinked ticket gets no button; publishing stays in Details", async () => {
  mockLink.mockResolvedValue(null);
  const { container } = renderButton();

  await waitFor(() => expect(mockLink).toHaveBeenCalledWith(TICKET));
  expect(container).toBeEmptyDOMElement();
});

test("a linked ticket syncs from its header and says what moved", async () => {
  mockLink.mockResolvedValue(LINKED);
  mockSync.mockResolvedValue(result({ pulled: ["title"] }));
  const user = userEvent.setup();
  renderButton();

  await user.click(await screen.findByRole("button", { name: "Sync with GitHub issue acme/widgets#42" }));

  expect(mockSync).toHaveBeenCalledWith(TICKET, "report");
  await waitFor(() => expect(toastTitles()).toEqual(["success:Synced with GitHub"]));
});

test("a conflict is not resolved here; the toast sends the operator to Details", async () => {
  mockLink.mockResolvedValue(LINKED);
  mockSync.mockResolvedValue(result({ conflicts: [{ field: "title" } as GithubLinkSyncResult["conflicts"][number]] }));
  const user = userEvent.setup();
  renderButton();

  await user.click(await screen.findByRole("button", { name: /Sync with GitHub issue/ }));

  await waitFor(() => expect(toastTitles()).toEqual(["warning:Changed on both sides"]));
  expect(useToastStore.getState().toasts[0].message).toMatch(/Open Details/);
});

test("a link that could not be read shows as unavailable, not as unlinked", async () => {
  mockLink.mockRejectedValue(new ApiError(500, "github down"));
  renderButton();

  const button = await screen.findByRole("button", { name: "GitHub unavailable" });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", expect.stringMatching(/Couldn't read this ticket's GitHub link/));
});
