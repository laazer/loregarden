import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../../api/client";
import { NewAgentDialog } from "../NewAgentDialog";

jest.mock("../../../api/client", () => ({
  api: { createStudioAgent: jest.fn(), generateStudioAgent: jest.fn() },
}));

const createAgent = api.createStudioAgent as jest.Mock;
const generateAgent = api.generateStudioAgent as jest.Mock;

function renderDialog() {
  const onCreated = jest.fn();
  const onClose = jest.fn();
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <NewAgentDialog open onClose={onClose} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { onCreated, onClose };
}

beforeEach(() => {
  createAgent.mockReset();
  generateAgent.mockReset();
});

describe("NewAgentDialog", () => {
  it("needs only a name: the slug follows it, and the editor opens on the new agent", async () => {
    createAgent.mockResolvedValue({ slug: "release-notes" });
    const { onCreated } = renderDialog();

    expect(screen.getByRole("button", { name: "Create agent" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Name"), "Release notes");
    expect(screen.getByLabelText("Slug")).toHaveValue("release-notes");
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(createAgent).toHaveBeenCalledWith({ name: "Release notes", slug: "release-notes", description: "" });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("release-notes"));
  });

  it("keeps a typed slug once it has been edited", async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText("Slug"), "notes");
    await userEvent.type(screen.getByLabelText("Name"), "Release notes");
    expect(screen.getByLabelText("Slug")).toHaveValue("notes");
  });

  it("drafts from a description and carries the drafted role into the create", async () => {
    generateAgent.mockResolvedValue({
      name: "Localization Reviewer",
      slug: "localization-reviewer",
      description: "Reviews localized copy",
      role_body: "Review diffs for i18n.",
      adapter: "claude",
      default_skill: "review",
      mcp_tools: ["loregarden_get_ticket"],
    });
    createAgent.mockResolvedValue({ slug: "localization-reviewer" });
    renderDialog();

    await userEvent.type(screen.getByLabelText("Describe it (optional)"), "checks translations");
    await userEvent.click(screen.getByRole("button", { name: "Draft from this" }));
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Localization Reviewer"));
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(createAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        slug: "localization-reviewer",
        role_body: "Review diffs for i18n.",
        mcp_tools: ["loregarden_get_ticket"],
        mcp_enabled: true,
      }),
    );
  });

  it("keeps the fields and says why when the server refuses", async () => {
    createAgent.mockRejectedValue(new Error("Slug conflicts with built-in agent: planner"));
    const { onCreated } = renderDialog();
    await userEvent.type(screen.getByLabelText("Name"), "planner");
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("conflicts with built-in agent");
    expect(screen.getByLabelText("Name")).toHaveValue("planner");
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("holds the dialog while the create is in flight, so it cannot fire twice", async () => {
    createAgent.mockReturnValue(new Promise(() => {}));
    const { onClose } = renderDialog();
    await userEvent.type(screen.getByLabelText("Name"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(screen.getByRole("button", { name: "Creating…" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
  });
});
