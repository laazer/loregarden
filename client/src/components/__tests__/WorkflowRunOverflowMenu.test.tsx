import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, type TicketDetail } from "../../api/client";
import { useToastStore } from "../../state/toastStore";
import { WorkflowRunOverflowMenu } from "../WorkflowRunOverflowMenu";

jest.mock("../../api/client", () => require("../../test/apiClientMock"));

const mockApi = api as jest.Mocked<typeof api>;

const TICKET = {
  id: "t-1",
  external_id: "07-external-harness",
  title: "Hand a ticket to an outside harness",
  run_code: "",
  workflow_template_slug: "studio-loregarden-tdd-v3",
} as unknown as TicketDetail;

function open() {
  render(
    <WorkflowRunOverflowMenu
      ticket={TICKET}
      orchestrateCommand="curl ..."
      rerunDisabled={false}
      onRerun={jest.fn()}
      onDelete={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "More workflow actions" }));
}

describe("WorkflowRunOverflowMenu — external harness prompts", () => {
  const writeText = jest.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    jest.clearAllMocks();
    useToastStore.getState().clear();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
  });

  it("offers one item per harness", () => {
    open();

    expect(screen.getByRole("menuitem", { name: "Claude Code" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Codex" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Cursor" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Other harness" })).toBeTruthy();
  });

  it("copies the prompt the server rendered for the chosen harness", async () => {
    mockApi.buildExternalHarnessPrompt.mockResolvedValue({
      harness: "codex",
      ticket_id: "t-1",
      external_id: "07-external-harness",
      workspace_slug: "loregarden",
      prompt: "# Loregarden ticket handoff",
    });

    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Codex" }));

    await waitFor(() =>
      expect(mockApi.buildExternalHarnessPrompt).toHaveBeenCalledWith("t-1", "codex"),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("# Loregarden ticket handoff"));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.tone)).toContain("success"),
    );
  });

  it("reports a failed copy instead of claiming success", async () => {
    mockApi.buildExternalHarnessPrompt.mockRejectedValue(new Error("workspace repo missing"));

    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Claude Code" }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.map((t) => t.tone)).toContain("error");
      expect(toasts.some((t) => t.message.includes("workspace repo missing"))).toBe(true);
    });
    expect(writeText).not.toHaveBeenCalled();
  });
});
