import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { SiblingClaim, SiblingClaimsView } from "../../../api/ticketEdgeApi";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { SharedFilesCard } from "../SharedFilesCard";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const TICKET = { id: "t1", external_id: "lg-t1" } as TicketDetail;

function sibling(n: number, overrides: Partial<SiblingClaim> = {}): SiblingClaim {
  return {
    ticket_id: `s${n}`,
    external_id: `lg-sib-${n}`,
    title: `Sibling ${n}`,
    state: "in_progress",
    ordered: false,
    paths: [{ path: "server/loregarden/services/land_ticket.py", source: "observed" }],
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SharedFilesCard ticket={TICKET} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function reply(view: SiblingClaimsView) {
  mockApi.siblingClaims.mockResolvedValue(view);
}

beforeEach(() => jest.clearAllMocks());

it("says a ticket claiming no files has nothing to compare yet", async () => {
  reply({ claimed_paths: 0, siblings: [] });
  renderCard();

  expect(await screen.findByText(/Claims no files yet/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Wait for/ })).not.toBeInTheDocument();
});

it("says no sibling shares the files, naming how many it claims", async () => {
  reply({ claimed_paths: 3, siblings: [] });
  renderCard();

  expect(await screen.findByText(/any of the 3 files/)).toBeInTheDocument();
});

it("at the largest live volume, lists every sibling with its files, folding past four", async () => {
  // Live on 2026-11-20: at most 4 siblings overlap one ticket, sharing up to 11 files.
  const many = Array.from({ length: 11 }, (_, i) => ({ path: `server/f${i}.py`, source: "planned" as const }));
  reply({
    claimed_paths: 14,
    siblings: [sibling(1, { paths: many }), sibling(2), sibling(3), sibling(4, { ordered: true })],
  });
  const { container } = renderCard();

  const first = within(await screen.findByRole("list", { name: "Files shared with lg-sib-1" }));
  expect(first.getAllByRole("listitem")).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Show all 11 files" }));
  expect(first.getAllByRole("listitem")).toHaveLength(11);
  expect(screen.getByText("3 not ordered")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sibling 1" })).toHaveAttribute("href", expect.stringContaining("s1"));
  expect(findUsabilityProblems(container)).toEqual([]);
});

it("names how the sibling claims each file, in words", async () => {
  reply({
    claimed_paths: 2,
    siblings: [
      sibling(1, {
        paths: [
          { path: "a.py", source: "observed" },
          { path: "b.py", source: "declared" },
          { path: "c.py", source: "planned" },
        ],
      }),
    ],
  });
  renderCard();

  const files = within(await screen.findByRole("list", { name: "Files shared with lg-sib-1" }));
  expect(files.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "a.pychanged",
    "b.pyabout to change",
    "c.pyplanned",
  ]);
});

it("Wait for it links this ticket to wait for the sibling, once, and refreshes the card", async () => {
  reply({ claimed_paths: 1, siblings: [sibling(1)] });
  let settle: (detail: TicketDetail) => void = () => {};
  mockApi.addDependency.mockReturnValue(new Promise((resolve) => (settle = resolve)));
  renderCard();

  const wait = await screen.findByRole("button", { name: "Wait for lg-sib-1 to land before this ticket runs" });
  fireEvent.click(wait);

  await waitFor(() => expect(mockApi.addDependency).toHaveBeenCalledWith("t1", "s1"));
  expect(screen.getByRole("button", { name: /Wait for lg-sib-1/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /Wait for lg-sib-1/ }));
  reply({ claimed_paths: 1, siblings: [sibling(1, { ordered: true })] });
  settle(TICKET);
  expect(await screen.findByText("Ordered")).toBeInTheDocument();
  expect(mockApi.addDependency).toHaveBeenCalledTimes(1);
});

it("an ordered sibling offers no Wait", async () => {
  reply({ claimed_paths: 1, siblings: [sibling(1, { ordered: true })] });
  renderCard();

  expect(await screen.findByText("Ordered")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Wait for/ })).not.toBeInTheDocument();
});

it("a failed check says so where the answer would be, and retries", async () => {
  mockApi.siblingClaims.mockRejectedValueOnce(new Error("database is locked"));
  renderCard();

  expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
  reply({ claimed_paths: 0, siblings: [] });
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText(/Claims no files yet/)).toBeInTheDocument();
});
