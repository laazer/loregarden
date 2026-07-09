import { render, screen } from "@testing-library/react";

import { TRIAGE_AGENT_NAME } from "../../../lib/triageAgent";
import { ChatMessageBubble } from "../ChatMessageBubble";
import { ChatWindow } from "../ChatWindow";
import { chatMessageBody, chatRoleLabel, formatChatTime, normalizeChatMarkdown } from "../chatUtils";

describe("chatUtils", () => {
  it("labels roles consistently", () => {
    expect(chatRoleLabel("user")).toBe("You");
    expect(chatRoleLabel("assistant", "Scoper")).toBe("Scoper");
  });

  it("prefers display_content for message body", () => {
    expect(
      chatMessageBody({
        id: "1",
        role: "assistant",
        content: '{"summary":"x"}',
        display_content: "3 draft tickets proposed",
      }),
    ).toBe("3 draft tickets proposed");
  });

  it("formats timestamps", () => {
    expect(formatChatTime("2026-07-06T20:15:00.000Z")).toMatch(/\d/);
    expect(formatChatTime()).toBe("");
  });

  it("normalizes single newlines for markdown hard breaks", () => {
    expect(normalizeChatMarkdown("line one\nline two")).toBe("line one  \nline two");
    expect(normalizeChatMarkdown("para one\n\npara two")).toBe("para one\n\npara two");
  });

  it("preserves markdown table rows without hard-break conversion", () => {
    const table = [
      "| Situation | Tool |",
      "|-----------|------|",
      "| Read ticket | `loregarden_get_ticket` |",
    ].join("\n");

    expect(normalizeChatMarkdown(`Intro line\n\n${table}`)).toBe(`Intro line\n\n${table}`);
    expect(normalizeChatMarkdown(`Before\n${table}\nAfter`)).toBe(
      `Before\n\n${table}\n\nAfter`,
    );
  });
});

describe("ChatMessageBubble", () => {
  it("renders user and assistant bubbles with shared classes", () => {
    const { rerender } = render(
      <ChatMessageBubble
        message={{
          id: "1",
          role: "user",
          content: "Hello",
          created_at: "2026-07-06T20:15:00.000Z",
        }}
      />,
    );

    expect(screen.getByText("Hello").closest(".chat-message-user")).toBeInTheDocument();

    rerender(
      <ChatMessageBubble
        message={{
          id: "2",
          role: "assistant",
          content: "Hi there",
          created_at: "2026-07-06T20:16:00.000Z",
        }}
        assistantLabel={TRIAGE_AGENT_NAME}
      />,
    );

    expect(screen.getByText("Hi there").closest(".chat-message-assistant")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(TRIAGE_AGENT_NAME))).toBeInTheDocument();
  });

  it("renders markdown formatting in bubbles", () => {
    render(
      <ChatMessageBubble
        message={{
          id: "3",
          role: "assistant",
          content: "**Bold claim** with `inline code`",
        }}
        assistantLabel="Scoper"
      />,
    );

    const bold = screen.getByText("Bold claim");
    expect(bold.tagName).toBe("STRONG");
    expect(screen.getByText("inline code").tagName).toBe("CODE");
  });
});

describe("ChatWindow", () => {
  it("shows empty state and thinking indicator", () => {
    const { rerender } = render(
      <ChatWindow
        title="Scope chat"
        messages={[]}
        emptyMessage="Start chatting"
        assistantLabel="Scoper"
      />,
    );

    expect(screen.getByText("Scope chat")).toBeInTheDocument();
    expect(screen.getByText("Start chatting")).toBeInTheDocument();

    rerender(
      <ChatWindow
        messages={[{ id: "1", role: "assistant", content: "Ready" }]}
        isThinking
        thinkingMessage="Scoper is thinking…"
      />,
    );

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Scoper is thinking…")).toBeInTheDocument();
  });
});
