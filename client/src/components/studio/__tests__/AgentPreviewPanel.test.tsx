import { fireEvent, render, screen } from "@testing-library/react";

import type { StudioAgentPreview } from "../../../api/client";
import { AgentPreviewPanel } from "../AgentPreviewPanel";

const preview: StudioAgentPreview = {
  name: "Preview Bot",
  markdown: "## Agent Role\n\nReview staged diffs carefully.",
  sections: ["header", "role", "mcp_tools", "gates", "handoffs", "permissions"],
  profile: {
    description: "Reviews staged diffs against acceptance criteria.",
    model: "claude-3.7-sonnet",
    provider: "claude",
    default_skill: "review",
    timeout: 600,
    always_apply: null,
  },
};

describe("AgentPreviewPanel", () => {
  it("renders preview content without window chrome dots", () => {
    const { container } = render(
      <AgentPreviewPanel preview={preview} loading={false} slug="preview-bot" />,
    );

    expect(screen.getByText("Live assembled prompt")).toBeInTheDocument();
    expect(screen.getByText("preview-bot.system.md")).toBeInTheDocument();
    expect(screen.getByText("Preview Bot")).toBeInTheDocument();
    expect(screen.getByText("Reviews staged diffs against acceptance criteria.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand/i })).toBeEnabled();
    expect(container.querySelector(".studio-preview-terminal-dot")).not.toBeInTheDocument();
    expect(container.querySelector(".studio-preview-chip--active")).toBeInTheDocument();
  });

  it("opens the full preview modal when expand is clicked", () => {
    render(<AgentPreviewPanel preview={preview} loading={false} slug="preview-bot" />);

    fireEvent.click(screen.getByRole("button", { name: /expand/i }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /assembled prompt/i })).toBeInTheDocument();
    expect(document.querySelector(".studio-preview-modal-scroll")).toBeInTheDocument();
    expect(screen.getAllByText("Review staged diffs carefully.").length).toBeGreaterThan(0);
  });

  it("disables expand when preview is empty", () => {
    render(<AgentPreviewPanel preview={undefined} loading={false} />);

    expect(screen.getByRole("button", { name: /expand/i })).toBeDisabled();
  });
});
