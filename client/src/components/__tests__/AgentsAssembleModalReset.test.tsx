import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import type { TicketDetail, WorkspaceRuntimeSettings } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { AgentsAssembleModal } from "../AgentsAssembleModal";

const RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "",
  claude_model: "",
  cursor_model: "",
  codex_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "ticket_1",
    external_id: "lg-dash-1",
    title: "Dash movement",
    description: "",
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
    tags: [],
    state: "in_progress",
    priority: 2,
    stages: [],
    branch: "",
    ...overrides,
  } as unknown as TicketDetail;
}

type Props = ComponentProps<typeof AgentsAssembleModal>;

function baseProps(overrides: Partial<Props> = {}): Props {
  return {
    open: true,
    ticket: makeTicket(),
    workspaceRuntime: { ...RUNTIME },
    runtimeOptions: undefined,
    stages: [],
    isRunning: false,
    isSavingRuntime: false,
    onClose: jest.fn(),
    onConfirm: jest.fn(),
    ...overrides,
  };
}

function autoApproveBox(): HTMLInputElement {
  return screen.getByRole("checkbox", { name: /Auto-approve CLI tool permissions and workflow gates/ });
}

describe("AgentsAssembleModal form reset", () => {
  it("keeps auto-approve ticked when the ticket and runtime are refetched as new objects", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = renderWithRouter(<AgentsAssembleModal {...baseProps()} />, { queryClient: client });

    fireEvent.click(autoApproveBox());
    expect(autoApproveBox().checked).toBe(true);

    // A poll or websocket update: same ticket, same runtime, fresh references.
    rerender(<AgentsAssembleModal {...baseProps({ ticket: makeTicket(), workspaceRuntime: { ...RUNTIME } })} />);

    expect(autoApproveBox().checked).toBe(true);
  });

  it("clears auto-approve when the dialog is reopened", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = renderWithRouter(<AgentsAssembleModal {...baseProps()} />, { queryClient: client });

    fireEvent.click(autoApproveBox());
    rerender(<AgentsAssembleModal {...baseProps({ open: false })} />);
    rerender(<AgentsAssembleModal {...baseProps()} />);

    expect(autoApproveBox().checked).toBe(false);
  });
});
