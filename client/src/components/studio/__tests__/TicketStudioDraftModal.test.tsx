import { fireEvent, render, screen } from "@testing-library/react";

import type { TicketStudioDraftItem } from "../../../api/client";
import { TicketStudioDraftModal } from "../TicketStudioDraftModal";

function mockAgents() {
  return [
    { slug: "backend_implementer", name: "Backend Implementer" },
    { slug: "planner", name: "Planner" },
  ] as const;
}

function mockItem(overrides: Partial<TicketStudioDraftItem> = {}): TicketStudioDraftItem {
  return {
    ref: "t1",
    work_item_type: "task",
    parent_ref: null,
    title: "Build API",
    description: "REST endpoints for sessions.",
    acceptance_criteria: ["POST /sessions returns 201"],
    priority: 2,
    suggested_agent: "backend_implementer",
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
        agentOptions={mockAgents() as never}
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
        agentOptions={mockAgents() as never}
        isOpen
        onClose={() => {}}
        onSave={() => {}}
      />,
    );

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Build API")).toBeInTheDocument();
    expect(screen.getByLabelText(/suggested agent/i)).toBeInTheDocument();
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
        agentOptions={mockAgents() as never}
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
        agentOptions={mockAgents() as never}
        isOpen
        readOnly
        onClose={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /save to draft/i })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("Build API")).toHaveAttribute("readonly");
  });

  it("keeps unknown scoped agents selectable", () => {
    render(
      <TicketStudioDraftModal
        item={mockItem({ suggested_agent: "custom_agent" })}
        allItems={[mockItem({ suggested_agent: "custom_agent" })]}
        agentOptions={mockAgents() as never}
        isOpen
        onClose={() => {}}
        onSave={() => {}}
      />,
    );

    expect(screen.getByRole("option", { name: /custom_agent \(from scope\)/i })).toBeInTheDocument();
  });
});
