import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { StudioChatMessages } from "../../studio/StudioChat";
import { ticketTitleFromReply } from "../../../hooks/useChatMessageActions";

jest.mock("../../../lib/clipboard", () => ({ copyText: jest.fn() }));
jest.mock("../../../state/toastStore", () => ({
  pushToast: jest.fn(),
  describeError: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
}));

const { copyText } = jest.requireMock("../../../lib/clipboard");
const { pushToast } = jest.requireMock("../../../state/toastStore");

const reply = [{ id: "m1", role: "assistant", content: "Here is the answer." }];

beforeEach(() => {
  jest.clearAllMocks();
  copyText.mockResolvedValue(undefined);
});

describe("per-reply actions", () => {
  it("offers nothing on a surface that passes no handlers", () => {
    render(<StudioChatMessages messages={reply} />);
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });

  it("copies the reply body", async () => {
    render(<StudioChatMessages messages={reply} messageActions={{}} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(copyText).toHaveBeenCalledWith("Here is the answer."));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ tone: "success" })),
    );
  });

  it("reports a copy that failed instead of looking like it worked", async () => {
    copyText.mockRejectedValue(new Error("Clipboard is blocked"));
    render(<StudioChatMessages messages={reply} messageActions={{}} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        expect.objectContaining({ tone: "error", message: "Clipboard is blocked" }),
      ),
    );
  });

  it("hides Fork on a surface with nothing to branch", () => {
    render(<StudioChatMessages messages={reply} messageActions={{}} />);
    expect(screen.queryByRole("button", { name: "Fork" })).not.toBeInTheDocument();
  });

  it("forks from the message it was pressed on", async () => {
    const onFork = jest.fn().mockResolvedValue(undefined);
    render(<StudioChatMessages messages={reply} messageActions={{ onFork }} />);
    fireEvent.click(screen.getByRole("button", { name: "Fork" }));
    expect(onFork).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Fork" })).toBeEnabled());
  });

  it("will not fire a second fork while the first is in flight", async () => {
    let settle: () => void = () => {};
    const onFork = jest.fn(() => new Promise<void>((resolve) => (settle = resolve)));
    render(<StudioChatMessages messages={reply} messageActions={{ onFork }} />);
    fireEvent.click(screen.getByRole("button", { name: "Fork" }));
    const forking = screen.getByRole("button", { name: "Forking…" });
    expect(forking).toBeDisabled();
    fireEvent.click(forking);
    expect(onFork).toHaveBeenCalledTimes(1);
    settle();
    await waitFor(() => expect(screen.getByRole("button", { name: "Fork" })).toBeEnabled());
  });

  it("starts a ticket from the overflow menu", async () => {
    const onStartTicket = jest.fn().mockResolvedValue(undefined);
    render(<StudioChatMessages messages={reply} messageActions={{ onStartTicket }} />);
    fireEvent.click(screen.getByRole("button", { name: "More actions for this reply" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start as ticket" }));
    expect(onStartTicket).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "More actions for this reply" })).toBeEnabled(),
    );
  });
});

describe("ticketTitleFromReply", () => {
  it("takes the first line of real prose", () => {
    expect(ticketTitleFromReply("Add a history API\nThen wire the UI")).toBe("Add a history API");
  });

  it("strips markdown chrome rather than carrying it into a ticket list", () => {
    expect(ticketTitleFromReply("## Plan\n- step one")).toBe("Plan");
    expect(ticketTitleFromReply("1. Add the endpoint")).toBe("Add the endpoint");
  });

  it("clips a long first line", () => {
    const title = ticketTitleFromReply("x".repeat(400));
    expect(title.length).toBe(120);
    expect(title.endsWith("…")).toBe(true);
  });

  it("has nothing to offer for a reply that is only a card", () => {
    expect(ticketTitleFromReply("")).toBe("");
  });
});
