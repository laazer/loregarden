/**
 * The agent card's role-text pane, and its keyboard path.
 *
 * lg-milestone-that-2215: `.lg-primitive-terminal-body` is a bounded
 * `overflow: auto` <pre> holding the first lines of the role file. The card's
 * controls are in the header above it and `Open in Agent Studio` is in the
 * footer below, so nothing inside the pane could take focus.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

import { AgentPrimitive } from "../AgentPrimitive";
import type { AgentPart } from "../types";
import { api } from "../../../../api/client";

jest.mock("../../../../api/client", () => ({
  api: { studioAgent: jest.fn(), previewStudioAgent: jest.fn() },
}));
const mockAgent = api.studioAgent as jest.MockedFunction<typeof api.studioAgent>;
const mockPreview = api.previewStudioAgent as jest.MockedFunction<typeof api.previewStudioAgent>;

const ROLE = ["# Planner", "", "You write plans.", "You never write code."].join("\n");

function renderCard(part: AgentPart) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
  return render(<AgentPrimitive part={part} />, { wrapper });
}

const REGION = "Agent role text";

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the agent card's role pane", () => {
  it("is a tab stop that steps on to Open in Agent Studio — AC3", async () => {
    // A saved agent rather than a draft, because a draft card renders no
    // controls at all — and AC3 is explicit that a test asserting a step that
    // does not exist does not satisfy it.
    mockAgent.mockResolvedValue({ slug: "planner" } as never);
    mockPreview.mockResolvedValue({
      name: "Planner",
      markdown: ROLE,
      sections: ["header", "role"],
      profile: {
        description: "",
        model: "",
        provider: "",
        default_skill: "",
        timeout: 600,
        always_apply: null,
      },
    } as never);
    renderCard({ primitive: "agent", slug: "planner", title: "Planner" } as AgentPart);

    const pane = await screen.findByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // `Open in Agent Studio` is in the footer, which FOLLOWS the pane, so Tab
    // forward is the step this surface offers.
    pane.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Open in Agent Studio" })).toHaveFocus();
  });

  it("puts a line in its place for an agent with no role text — AC8", () => {
    renderCard({
      primitive: "agent",
      title: "Planner",
      draft: { name: "Planner", role_body: "" },
    } as AgentPart);

    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("This agent has no role text yet.")).toBeInTheDocument();
  });
});
