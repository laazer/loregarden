import { screen, waitFor } from "@testing-library/react";

import { api } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { BringInChangesButton } from "../BringInChangesButton";

jest.mock("../../api/client", () => require("../../test/apiClientMock"));

const mockApi = api as jest.Mocked<typeof api>;

const READY = {
  workspace_slug: "loregarden",
  supported: true,
  ready: true,
  blockers: [],
  active_agent_runs: [],
  active_orchestrations: [],
  running_workflow_tickets: [],
};

describe("BringInChangesButton", () => {
  beforeEach(() => jest.clearAllMocks());

  it("renders nothing for a workspace that is not the server under test", async () => {
    mockApi.reloadStatus.mockResolvedValue({ ...READY, workspace_slug: "blobert", supported: false });

    const { container } = renderWithRouter(<BringInChangesButton workspaceSlug="blobert" />);

    await waitFor(() => expect(mockApi.reloadStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("triggers a reload and reports when the server is back", async () => {
    mockApi.reloadStatus.mockResolvedValue(READY);
    mockApi.reloadServer.mockResolvedValue({ triggered: true, at: "2026-07-16T20:16:35Z" });
    mockApi.health.mockResolvedValue({});

    renderWithRouter(<BringInChangesButton workspaceSlug="loregarden" />);

    const button = await screen.findByRole("button", { name: "Bring in changes" });
    button.click();

    await waitFor(() => expect(mockApi.reloadServer).toHaveBeenCalledWith("loregarden"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Changes are live" })).toBeTruthy(), {
      timeout: 5000,
    });
  });

  it("disables the button and names what is blocking while an agent is running", async () => {
    mockApi.reloadStatus.mockResolvedValue({
      ...READY,
      ready: false,
      blockers: ["active_agent_runs"],
      active_agent_runs: [{ id: "r1", run_code: "run_x", stage_key: "implement" }],
    });

    renderWithRouter(<BringInChangesButton workspaceSlug="loregarden" />);

    const button = await screen.findByRole("button", { name: "Bring in changes" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/an agent is running/)).toBeTruthy();
    expect(mockApi.reloadServer).not.toHaveBeenCalled();
  });

  it("surfaces a refusal instead of pretending the reload happened", async () => {
    mockApi.reloadStatus.mockResolvedValue(READY);
    const refusal = Object.assign(new Error("active_agent_runs"), { status: 409 });
    mockApi.reloadServer.mockRejectedValue(refusal);

    renderWithRouter(<BringInChangesButton workspaceSlug="loregarden" />);

    (await screen.findByRole("button", { name: "Bring in changes" })).click();

    await waitFor(() => expect(screen.getByRole("button", { name: "Retry reload" })).toBeTruthy());
    expect(mockApi.health).not.toHaveBeenCalled();
  });
});
