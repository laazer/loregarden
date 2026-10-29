/**
 * Instances and Usage open from the Machine page now that they have left the
 * topbar, and a near-limit warning still shows on the button that opens Usage.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { MachinePage } from "../MachinePage";

jest.mock("../../api/client", () => ({ api: { usage: jest.fn() } }));
jest.mock("../../components/machine/MachineCapacityView", () => ({
  MachineCapacityView: () => <div>capacity</div>,
}));
jest.mock("../../components/machine/DiskUsageView", () => ({ DiskUsageView: () => <div>disk</div> }));
jest.mock("../../components/LocalInstancesModal", () => ({
  LocalInstancesModal: ({ open }: { open: boolean }) =>
    open ? <div role="dialog" aria-label="Local instances" /> : null,
}));
jest.mock("../../components/UsageModal", () => ({
  UsageModal: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="Usage details" /> : null),
}));

const mockUsage = api.usage as jest.MockedFunction<typeof api.usage>;
type Usage = Awaited<ReturnType<typeof api.usage>>;

function renderMachine() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/machine"]}>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <MachinePage />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockUsage.mockResolvedValue({ near_limit: false } as Usage);
});

test("Instances opens the local instances dialog", async () => {
  const user = userEvent.setup();
  renderMachine();

  await user.click(await screen.findByRole("button", { name: "Instances" }));
  expect(screen.getByRole("dialog", { name: "Local instances" })).toBeInTheDocument();
});

test("Usage opens the usage dialog", async () => {
  const user = userEvent.setup();
  renderMachine();

  await user.click(await screen.findByRole("button", { name: "Usage" }));
  expect(screen.getByRole("dialog", { name: "Usage details" })).toBeInTheDocument();
});

test("a near limit is named on the Usage button", async () => {
  mockUsage.mockResolvedValue({ near_limit: true } as Usage);
  renderMachine();

  expect(
    await screen.findByRole("button", { name: "Usage — limits are getting close" }),
  ).toHaveClass("usage-btn-warning");
});
