import { render, screen } from "@testing-library/react";

import type { StudioAgentPreview } from "../../../api/client";
import { AgentPreviewContent } from "../AgentPreviewContent";

const preview: StudioAgentPreview = {
  name: "Preview Bot",
  markdown: "## Agent Role\n\nReview staged diffs carefully.",
  sections: ["header", "role", "mcp_tools", "gates", "handoffs", "permissions"],
  profile: {
    description: "Reviews staged diffs against acceptance criteria.",
    model: "claude-3.7-sonnet",
    default_skill: "review",
    timeout: 600,
    always_apply: null,
  },
};

describe("AgentPreviewContent", () => {
  it("renders the assembled prompt and its identity, with no provider row", () => {
    const { container } = render(
      <AgentPreviewContent preview={preview} loading={false} slug="preview-bot" />,
    );

    expect(screen.getByText("preview-bot.system.md")).toBeInTheDocument();
    expect(screen.getByText("Preview Bot")).toBeInTheDocument();
    expect(screen.getByText("Reviews staged diffs against acceptance criteria.")).toBeInTheDocument();
    expect(container.querySelector(".studio-preview-chip--active")).toBeInTheDocument();
    expect(screen.queryByText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
  });

  it("does not render Provider as an em dash", () => {
    render(<AgentPreviewContent preview={preview} loading={false} slug="preview-bot" />);
    expect(screen.queryByText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("ignores a leftover provider field on the profile", () => {
    const leftover = {
      ...preview,
      profile: { ...preview.profile, provider: "cursor" },
    } as StudioAgentPreview;
    render(<AgentPreviewContent preview={leftover} loading={false} slug="preview-bot" />);
    expect(screen.queryByText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByText("Cursor Agent")).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("keeps empty and loading copy", () => {
    const { rerender } = render(<AgentPreviewContent preview={undefined} loading={false} />);
    expect(
      screen.getByText("Select or edit an agent to see the assembled prompt."),
    ).toBeInTheDocument();
    rerender(<AgentPreviewContent preview={undefined} loading />);
    expect(screen.getByText("Updating preview…")).toBeInTheDocument();
  });
});
