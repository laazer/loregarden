import {
  buildTextDiffArtifact,
  formatEditCommentsForChat,
} from "../textDiff";

describe("buildTextDiffArtifact", () => {
  it("marks added and deleted lines", () => {
    const diff = buildTextDiffArtifact(
      "role.md",
      "keep\nold line\ntrail\n",
      "keep\nnew line\ntrail\n",
    );

    expect(diff.file_entries?.[0]).toEqual({ path: "role.md", add: 1, del: 1 });
    const kinds = (diff.sections?.[0].lines ?? [])
      .filter((line) => line.type !== "h")
      .map((line) => `${line.type}:${line.text}`);
    expect(kinds).toEqual(["c:keep", "d:old line", "a:new line", "c:trail"]);
  });

  it("treats an empty original as a pure add", () => {
    const diff = buildTextDiffArtifact("new.md", "", "alpha\nbeta\n");
    expect(diff.add).toBe("+2");
    expect(diff.del).toBe("−0");
  });
});

describe("formatEditCommentsForChat", () => {
  it("builds a chat-ready review message", () => {
    const message = formatEditCommentsForChat({
      title: "Tighten Planner scope",
      path: "agent_context/agents/planner.md",
      comments: [
        {
          file_path: "agent_context/agents/planner.md",
          line_index: 4,
          line_kind: "a",
          line_number: "12",
          line_text: "You never write code.",
          content: "Make this a hard stop, not advice.",
        },
      ],
      instructions: "Revise and resend the edit card.",
    });

    expect(message).toContain("## Diff review: Tighten Planner scope");
    expect(message).toContain("File: `agent_context/agents/planner.md`");
    expect(message).toContain("L12 (+)");
    expect(message).toContain("Make this a hard stop, not advice.");
    expect(message).toContain("Revise and resend the edit card.");
  });
});
