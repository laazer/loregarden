import { fireEvent, render, screen } from "@testing-library/react";

import { LiveThinkingStream } from "../LiveThinkingStream";
import { StudioChatMessages } from "../../studio/StudioChat";

const mockThinking = jest.fn();
jest.mock("../../../hooks/useChatTurnThinking", () => ({
  useChatTurnThinking: (turnId: string | null | undefined) => mockThinking(turnId),
}));

beforeEach(() => {
  mockThinking.mockReturnValue({ content: "", answer: "", activity: "", isStreaming: false });
});

describe("LiveThinkingStream", () => {
  it("shows the activity line and the reasoning behind it", () => {
    render(<LiveThinkingStream content="Checking the fixture." activity="Read · a.py" />);

    expect(screen.getByText("Read · a.py")).toBeInTheDocument();
    expect(screen.getByText("Checking the fixture.")).toBeInTheDocument();
  });

  it("renders tool steps apart from reasoning prose", () => {
    // The transcript is one string; the steps have to survive the round trip
    // as steps, or a tool call reads as something the model said.
    render(<LiveThinkingStream content={"Look here.\n\n· Bash · pytest -q\n\nThat failed."} activity="" />);

    const step = screen.getByText("Bash · pytest -q");
    expect(step.className).toContain("lg-thinking-stream-step");
    expect(screen.getByText("Look here.").className).toContain("lg-thinking-stream-prose");
    expect(screen.getByText("That failed.")).toBeInTheDocument();
  });

  it("collapses on request and brings the reasoning back", () => {
    render(<LiveThinkingStream content="Deep thoughts." activity="Thinking" />);

    fireEvent.click(screen.getByRole("button", { name: /Thinking/ }));
    expect(screen.queryByText("Deep thoughts.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Thinking/ }));
    expect(screen.getByText("Deep thoughts.")).toBeInTheDocument();
  });

  it("renders nothing at all when the turn has said nothing", () => {
    const { container } = render(<LiveThinkingStream content="   " activity="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("StudioChatMessages live thinking", () => {
  it("watches the turn in flight, and only while it is in flight", () => {
    const { rerender } = render(
      <StudioChatMessages messages={[]} isThinking activeTurnId="turn-9" />,
    );
    expect(mockThinking).toHaveBeenLastCalledWith("turn-9");

    rerender(<StudioChatMessages messages={[]} isThinking={false} activeTurnId="turn-9" />);
    expect(mockThinking).toHaveBeenLastCalledWith(null);
  });

  it("replaces the pacing placeholder once the agent says what it is doing", () => {
    mockThinking.mockReturnValue({
      content: "Reading the runner.",
      answer: "",
      activity: "Read · runner.py",
      isStreaming: true,
    });

    render(
      <StudioChatMessages
        messages={[]}
        isThinking
        activeTurnId="turn-9"
        thinkingSub="Fetching a reply"
      />,
    );

    expect(screen.getByText("Reading the runner.")).toBeInTheDocument();
    expect(screen.queryByText("Fetching a reply")).not.toBeInTheDocument();
  });

  it("streams the reply itself when a turn produces no reasoning", () => {
    // Read-only turns emit an empty thinking block; the reply is the only
    // thing that moves, and without it the panel would sit blank.
    mockThinking.mockReturnValue({
      content: "",
      answer: "A dark mode ticket should cover",
      activity: "Writing the reply",
      isStreaming: true,
    });

    render(
      <StudioChatMessages
        messages={[]}
        isThinking
        activeTurnId="turn-9"
        thinkingSub="Fetching a reply"
      />,
    );

    expect(screen.getByText("A dark mode ticket should cover")).toBeInTheDocument();
    expect(screen.queryByText("Fetching a reply")).not.toBeInTheDocument();
  });

  it("keeps the placeholder while the turn has produced nothing yet", () => {
    render(
      <StudioChatMessages
        messages={[]}
        isThinking
        activeTurnId="turn-9"
        thinkingSub="Fetching a reply"
      />,
    );

    expect(screen.getByText("Fetching a reply")).toBeInTheDocument();
  });

  it("leads a settled reply with the reasoning that produced it", () => {
    render(
      <StudioChatMessages
        messages={[
          {
            id: "m1",
            role: "assistant",
            content: "Fixed it.",
            parts: [
              { primitive: "thinking", content: "It was the fixture." },
              { primitive: "text", content: "Fixed it." },
            ],
          },
        ]}
        showAssistantAvatar={false}
      />,
    );

    const turn = screen.getByText("Fixed it.").closest(".lg-chat-turn");
    const rendered = turn?.textContent ?? "";
    expect(rendered.indexOf("Thinking")).toBeLessThan(rendered.indexOf("Fixed it."));
  });
});
