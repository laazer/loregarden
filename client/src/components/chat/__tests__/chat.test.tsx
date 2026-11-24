import { chatMessageBody, chatRoleLabel, normalizeChatMarkdown } from "../chatUtils";

describe("chatUtils", () => {
  it("labels roles consistently", () => {
    expect(chatRoleLabel("user")).toBe("You");
    expect(chatRoleLabel("assistant", "Scoper")).toBe("Scoper");
  });

  it("does not attribute a system message to the assistant", () => {
    // A publish note ("Committed to chat/... and pushed") is the control plane
    // reporting on itself. Falling through to the assistant label would put
    // words in the agent's mouth that it never said.
    expect(chatRoleLabel("system", "Scoper")).toBe("Loregarden");
    expect(chatRoleLabel("system")).toBe("Loregarden");
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
