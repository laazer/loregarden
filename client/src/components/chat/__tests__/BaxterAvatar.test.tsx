import { act, render, screen } from "@testing-library/react";

import { BaxterAvatar } from "../BaxterAvatar";
import { StudioChatMessages } from "../../studio/StudioChat";

describe("BaxterAvatar", () => {
  it("renders idle Baxter with spritesheet state", () => {
    render(<BaxterAvatar state="idle" label="Scoper" />);
    const avatar = screen.getByRole("img", { name: "Scoper" });
    expect(avatar).toHaveAttribute("data-baxter-state", "idle");
    expect(avatar.className).toContain("baxter-avatar--idle");
  });

  it.each([
    ["thinking", "thinking"],
    ["typing", "typing"],
    ["responding", "responding"],
  ] as const)("applies %s animation class for %s state", (state, expected) => {
    render(<BaxterAvatar state={state} />);
    expect(screen.getByRole("img", { name: "Baxter" })).toHaveAttribute("data-baxter-state", expected);
    expect(screen.getByRole("img", { name: "Baxter" }).className).toContain(`baxter-avatar--${expected}`);
  });
});

describe("StudioChatMessages Baxter wiring", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("shows thinking Baxter beside the thinking indicator", () => {
    render(
      <StudioChatMessages
        messages={[]}
        isThinking
        thinkingMessage="Scoper is thinking…"
        thinkingActivity="thinking"
        assistantLabel="Scoper"
      />,
    );

    expect(screen.getByText("Scoper is thinking…")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Scoper" })).toHaveAttribute("data-baxter-state", "thinking");
  });

  it("uses typing animation when thinkingActivity is typing", () => {
    render(
      <StudioChatMessages
        messages={[{ id: "a1", role: "assistant", content: "Earlier reply" }]}
        isThinking
        thinkingActivity="typing"
        assistantLabel="Triage assistant"
      />,
    );

    const avatars = screen.getAllByRole("img", { name: "Triage assistant" });
    expect(avatars.some((el) => el.getAttribute("data-baxter-state") === "typing")).toBe(true);
  });

  it("flashes responding on the newest assistant message after a reply arrives", () => {
    const { rerender } = render(
      <StudioChatMessages
        messages={[{ id: "a1", role: "assistant", content: "First" }]}
        isThinking
        assistantLabel="Scoper"
      />,
    );

    rerender(
      <StudioChatMessages
        messages={[
          { id: "a1", role: "assistant", content: "First" },
          { id: "a2", role: "assistant", content: "Second" },
        ]}
        isThinking={false}
        assistantLabel="Scoper"
      />,
    );

    const responding = screen
      .getAllByRole("img", { name: "Scoper" })
      .find((el) => el.getAttribute("data-baxter-state") === "responding");
    expect(responding).toBeTruthy();

    act(() => {
      jest.advanceTimersByTime(1700);
    });

    expect(
      screen.getAllByRole("img", { name: "Scoper" }).every((el) => el.getAttribute("data-baxter-state") === "idle"),
    ).toBe(true);
  });
});
