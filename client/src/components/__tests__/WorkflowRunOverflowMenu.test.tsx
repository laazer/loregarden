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

const writeText = jest.fn();

/** A clipboard that accepts the write, and an empty toast stack. */
function stubClipboard() {
  jest.clearAllMocks();
  writeText.mockResolvedValue(undefined);
  useToastStore.getState().clear();
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

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
  beforeEach(stubClipboard);

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

describe("S7.3 — the orchestrate command's copy reports a refusal", () => {
  beforeEach(stubClipboard);

  it("copies the command when the clipboard takes it", async () => {
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy orchestrate command" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("curl ..."));
  });

  it("reports a clipboard that refused rather than floating the rejection", async () => {
    // `onSelect={() => void copyText(...)}` was a bare floating promise. Once
    // `copyText` rejects on a refusal (S7.1) that becomes an unhandled
    // rejection — a failure the operator never sees, in the one place whose
    // whole purpose is to put a command on their clipboard.
    writeText.mockRejectedValue(new Error("Document is not focused"));
    Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });

    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy orchestrate command" }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.map((t) => t.tone)).toContain("error");
      expect(toasts.some((t) => t.title === "Copy orchestrate command failed")).toBe(true);
    });
  });
});
