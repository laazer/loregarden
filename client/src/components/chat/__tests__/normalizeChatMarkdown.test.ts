import { normalizeChatMarkdown } from "../chatUtils";

describe("normalizeChatMarkdown", () => {
  it("keeps hard breaks inside a prose paragraph", () => {
    expect(normalizeChatMarkdown("first line\nsecond line")).toBe("first line  \nsecond line");
  });

  it("separates a list from the line that introduces it", () => {
    // A plan or acceptance-criteria block is written this way — no blank line
    // before the bullets. Hard-breaking them left the `- ` markers as literal
    // text instead of a list.
    const normalized = normalizeChatMarkdown("Acceptance criteria:\n- Cooldown\n- No clipping");

    expect(normalized).toBe("Acceptance criteria:\n\n- Cooldown\n- No clipping");
  });

  it("separates headings, ordered lists and quotes the same way", () => {
    expect(normalizeChatMarkdown("Plan:\n## Steps\n1. Read\n2. Write")).toBe(
      "Plan:\n\n## Steps\n1. Read\n2. Write",
    );
    expect(normalizeChatMarkdown("Note:\n> careful")).toBe("Note:\n\n> careful");
  });

  it("keeps indented continuation lines with their list item", () => {
    const normalized = normalizeChatMarkdown("- item\n  continued\n- next");

    expect(normalized).toBe("- item\n  continued\n- next");
  });

  it("leaves fenced code verbatim instead of injecting hard breaks", () => {
    const fenced = "```py\nx = 1\ny = 2\n```";

    expect(normalizeChatMarkdown(fenced)).toBe(fenced);
  });

  it("still isolates tables", () => {
    const normalized = normalizeChatMarkdown("Results:\n| a | b |\n| - | - |");

    expect(normalized).toBe("Results:\n\n| a | b |\n| - | - |");
  });

  it("does not treat a hyphenated sentence as a list", () => {
    expect(normalizeChatMarkdown("well-formed input\nis fine")).toBe(
      "well-formed input  \nis fine",
    );
  });
});
