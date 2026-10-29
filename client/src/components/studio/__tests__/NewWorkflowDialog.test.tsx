import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../../api/client";
import type { StudioWorkflow } from "../../../api/types";
import { NewWorkflowDialog } from "../NewWorkflowDialog";

jest.mock("../../../api/client", () => ({
  api: { createStudioWorkflow: jest.fn(), generateStudioWorkflow: jest.fn() },
}));

const createWorkflow = api.createStudioWorkflow as jest.Mock;
const generateWorkflow = api.generateStudioWorkflow as jest.Mock;

const TDD = {
  slug: "loregarden-tdd",
  name: "Loregarden TDD",
  stages: [
    { key: "plan", order: 4 },
    { key: "done", order: 9, terminal: true },
  ],
  transitions: [{ from: "plan", to: "done", when: "pass" }],
} as unknown as StudioWorkflow;

function renderDialog() {
  const onCreated = jest.fn();
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <NewWorkflowDialog open onClose={jest.fn()} onCreated={onCreated} workflows={[TDD]} />
    </QueryClientProvider>,
  );
  return { onCreated };
}

beforeEach(() => {
  createWorkflow.mockReset().mockResolvedValue({ slug: "release-flow" });
  generateWorkflow.mockReset();
});

describe("NewWorkflowDialog", () => {
  it("creates a blank workflow with no stages, then opens its editor", async () => {
    const { onCreated } = renderDialog();
    await userEvent.type(screen.getByLabelText("Name"), "Release flow");
    await userEvent.click(screen.getByRole("button", { name: "Create workflow" }));

    expect(createWorkflow).toHaveBeenCalledWith({
      name: "Release flow",
      slug: "release-flow",
      description: "",
      stages: [],
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("release-flow"));
  });

  it("copies another workflow's stages and routes, renumbered from 1", async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText("Name"), "Release flow");
    fireEvent.change(screen.getByLabelText("Start from"), { target: { value: "loregarden-tdd" } });
    await userEvent.click(screen.getByRole("button", { name: "Create workflow" }));

    const body = createWorkflow.mock.calls[0][0];
    expect(body.stages.map((stage: { key: string; order: number }) => [stage.key, stage.order])).toEqual([
      ["plan", 1],
      ["done", 2],
    ]);
    expect(body.transitions).toEqual(TDD.transitions);
  });

  it("uses a draft's stages in place of the start it was given", async () => {
    generateWorkflow.mockResolvedValue({
      name: "Docs flow",
      slug: "docs-flow",
      description: "Writes docs",
      stages: [{ key: "write", order: 1, terminal: true }],
    });
    renderDialog();
    fireEvent.change(screen.getByLabelText("Start from"), { target: { value: "loregarden-tdd" } });
    await userEvent.type(screen.getByLabelText("Describe it (optional)"), "docs");
    await userEvent.click(screen.getByRole("button", { name: "Draft from this" }));
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Docs flow"));
    await userEvent.click(screen.getByRole("button", { name: "Create workflow" }));

    const body = createWorkflow.mock.calls[0][0];
    expect(body.slug).toBe("docs-flow");
    expect(body.stages.map((stage: { key: string }) => stage.key)).toEqual(["write"]);
    expect(body.transitions).toBeUndefined();
  });
});
