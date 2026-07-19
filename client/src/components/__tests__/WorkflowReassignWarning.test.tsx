import { fireEvent, render, screen } from "@testing-library/react";

import type { WorkflowReassignmentPreview } from "../../api/client";
import { WorkflowReassignWarning } from "../WorkflowReassignWarning";

function preview(overrides: Partial<WorkflowReassignmentPreview> = {}): WorkflowReassignmentPreview {
  return {
    destructive: true,
    current_stage_key: "implement",
    current_template_slug: "loregarden-tdd",
    target_template_slug: "extended-tdd",
    target_template_name: "Extended TDD",
    completed_stages: ["planning", "specification", "implement"],
    resets_to_stage_key: "planning",
    ...overrides,
  };
}

describe("WorkflowReassignWarning", () => {
  it("stays out of the way when nothing would be lost", () => {
    // Assigning a workflow at commit time must not prompt.
    const { container } = render(
      <WorkflowReassignWarning
        preview={preview({ destructive: false, completed_stages: [] })}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing without a preview", () => {
    const { container } = render(
      <WorkflowReassignWarning preview={null} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names the stages that will be reset", () => {
    // The old behaviour discarded progress silently; naming it is the point.
    render(
      <WorkflowReassignWarning preview={preview()} onConfirm={() => {}} onCancel={() => {}} />,
    );
    // Scoped to the list: "planning" is also the restart stage in the sentence below.
    const listed = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(listed).toEqual(["planning", "specification", "implement"]);
  });

  it("says which workflow it is switching to and where it restarts", () => {
    render(
      <WorkflowReassignWarning preview={preview()} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(screen.getByText(/Restart this ticket on Extended TDD/)).toBeInTheDocument();
    expect(screen.getByText(/It will restart at/)).toBeInTheDocument();
  });

  it("warns rather than blocks — the change is still available", () => {
    const onConfirm = jest.fn();
    render(
      <WorkflowReassignWarning preview={preview()} onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /switch and reset/i }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("keeps the current workflow when cancelled", () => {
    const onCancel = jest.fn();
    const onConfirm = jest.fn();
    render(
      <WorkflowReassignWarning preview={preview()} onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /keep current workflow/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cannot be double-submitted while the switch is in flight", () => {
    render(
      <WorkflowReassignWarning
        preview={preview()}
        isPending
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /switching/i })).toBeDisabled();
  });
});
