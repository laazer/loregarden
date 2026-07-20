import { fireEvent, render, screen } from "@testing-library/react";

import type { TicketStudioDraftItem } from "../../../api/client";
import { TicketStudioDraftModal } from "../TicketStudioDraftModal";

function mockItem(overrides: Partial<TicketStudioDraftItem> = {}): TicketStudioDraftItem {
  return {
    ref: "t1",
    work_item_type: "task",
    parent_ref: null,
    title: "Build API",
    description: "REST endpoints for sessions.",
    acceptance_criteria: ["POST /sessions returns 201"],
    priority: 2,
    selected: true,
    ...overrides,
  };
}

describe("TicketStudioDraftModal", () => {
  it("renders nothing when closed", () => {
    render(
      <TicketStudioDraftModal
        item={mockItem()}
        allItems={[mockItem()]}
        workflowOptions={[]}
        isOpen={false}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows draft fields when open", () => {
    render(
      <TicketStudioDraftModal
        item={mockItem()}
        allItems={[mockItem(), mockItem({ ref: "t2", title: "Parent feature" })]}
        workflowOptions={[]}
        isOpen
        onClose={() => {}}
        onSave={() => {}}
      />,
    );

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Build API")).toBeInTheDocument();
    expect(screen.getByDisplayValue("REST endpoints for sessions.")).toBeInTheDocument();
    expect(screen.getByDisplayValue("POST /sessions returns 201")).toBeInTheDocument();
    expect(screen.getByLabelText(/parent/i)).toBeInTheDocument();
  });

  it("saves edited draft and closes", () => {
    const onSave = jest.fn();
    const onClose = jest.fn();

    render(
      <TicketStudioDraftModal
        item={mockItem()}
        allItems={[mockItem()]}
        workflowOptions={[]}
        isOpen
        onClose={onClose}
        onSave={onSave}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^type$/i), { target: { value: "feature" } });
    fireEvent.change(screen.getByDisplayValue("Build API"), { target: { value: "Build REST API" } });
    fireEvent.click(screen.getByRole("button", { name: /save to draft/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        work_item_type: "feature",
        title: "Build REST API",
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("is read-only without save button", () => {
    render(
      <TicketStudioDraftModal
        item={mockItem()}
        allItems={[mockItem()]}
        workflowOptions={[]}
        isOpen
        readOnly
        onClose={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /save to draft/i })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("Build API")).toHaveAttribute("readonly");
  });

});

describe("TicketStudioDraftModal workflow picker", () => {
  const workflows = [
    { slug: "loregarden-tdd", name: "Loregarden TDD" },
    { slug: "extended-tdd", name: "Extended TDD" },
  ] as never;

  function open(onSave = jest.fn(), item = mockItem()) {
    render(
      <TicketStudioDraftModal
        item={item}
        allItems={[item]}
        workflowOptions={workflows}
        isOpen
        onClose={() => {}}
        onSave={onSave}
      />,
    );
    return onSave;
  }

  it("defaults to the workspace default when no workflow is chosen", () => {
    open();
    const select = screen.getByLabelText("Workflow") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: "Workspace default" })).toBeInTheDocument();
  });

  it("saves the chosen workflow on the draft item", () => {
    const onSave = open();
    fireEvent.change(screen.getByLabelText("Workflow"), { target: { value: "extended-tdd" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_template_slug: "extended-tdd" }),
    );
  });

  it("enables save when only the workflow changed", () => {
    // draftEquals omitted this field, so a workflow-only edit would not enable Save.
    open();
    const save = screen.getByRole("button", { name: /save/i });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Workflow"), { target: { value: "extended-tdd" } });
    expect(save).not.toBeDisabled();
  });

  it("keeps showing a slug the workspace no longer offers", () => {
    open(jest.fn(), mockItem({ workflow_template_slug: "retired-template" }));
    const select = screen.getByLabelText("Workflow") as HTMLSelectElement;
    expect(select.value).toBe("retired-template");
    expect(screen.getByRole("option", { name: /retired-template \(not found\)/ })).toBeInTheDocument();
  });
});
