import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "../../../api/http";
import { ImportTicketsFlow } from "../ImportTicketsFlow";

const mockApi = {
  previewTicketImportPaths: jest.fn(),
  importTickets: jest.fn(),
  tickets: jest.fn(),
};
jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return { ...actual, get api() { return mockApi; } };
});

const mockNavigateToTicket = jest.fn();
jest.mock("../../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../../lib/useAppNavigation"),
  navigateToTicket: (...args: unknown[]) => mockNavigateToTicket(...args),
}));

/* The picker browses the server's files; what follows a choice is this test's. */
jest.mock("../../ImportTicketsModal", () => ({
  ImportTicketsModal: ({ open, onContinue }: { open: boolean; onContinue: (paths: string[], mode: string) => void }) =>
    open ? <input aria-label="pick files" onFocus={() => onContinue(["a.md"], "regular")} /> : null,
}));

const TICKETS = [
  { title: "First", work_item_type: "milestone", source_label: "a.md" },
  { title: "Second", work_item_type: "milestone", source_label: "b.md" },
];
const PREVIEW = {
  tickets: TICKETS,
  errors: [],
  warnings: [],
  total: 2,
  by_type: { milestone: 2 },
  formats: ["md"],
  show_preview: true,
};

async function confirmImport() {
  const onClose = jest.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ImportTicketsFlow workspaceSlug="loregarden" browsePath="." onClose={onClose} />
    </QueryClientProvider>,
  );
  screen.getByLabelText("pick files").focus();
  await userEvent.click(await screen.findByRole("button", { name: "Import 2 items" }));
  return { onClose };
}

const rejections: unknown[] = [];
const record = (reason: unknown) => rejections.push(reason);

beforeEach(() => {
  jest.clearAllMocks();
  rejections.length = 0;
  process.on("unhandledRejection", record);
  mockApi.previewTicketImportPaths.mockResolvedValue(PREVIEW);
  mockApi.tickets.mockResolvedValue([]);
});
afterEach(() => {
  process.off("unhandledRejection", record);
});

it("opens the first ticket and closes when everything was created", async () => {
  mockApi.importTickets.mockResolvedValue({ created_count: 2, ticket_ids: ["t-1", "t-2"], errors: [] });
  const { onClose } = await confirmImport();

  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  expect(mockApi.importTickets).toHaveBeenCalledTimes(1);
  expect(mockNavigateToTicket).toHaveBeenCalledWith("t-1", { replace: true });
});

it("stays to say which tickets were not created, instead of leaving for one that was", async () => {
  mockApi.importTickets.mockResolvedValue({
    created_count: 1,
    ticket_ids: ["t-1"],
    errors: ["b.md: could not resolve parent work item"],
  });
  const { onClose } = await confirmImport();

  const dialog = await screen.findByRole("dialog", { name: "Some work items were not imported" });
  expect(dialog).toHaveTextContent("1 of 2 created");
  expect(within(dialog).getByRole("listitem")).toHaveTextContent("b.md: could not resolve parent work item");
  expect(within(dialog).queryByRole("button", { name: /^Import/ })).not.toBeInTheDocument();
  expect(mockNavigateToTicket).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();

  await userEvent.click(within(dialog).getByText("Close"));
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("keeps the draft and says why when the import itself fails", async () => {
  mockApi.importTickets.mockRejectedValue(new ApiError(409, "the database is being migrated"));
  const { onClose } = await confirmImport();

  expect(await screen.findByText("the database is being migrated")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Import 2 items" })).toBeEnabled();
  expect(onClose).not.toHaveBeenCalled();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(rejections).toEqual([]);
});
