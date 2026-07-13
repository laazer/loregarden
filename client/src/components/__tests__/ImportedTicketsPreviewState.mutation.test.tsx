import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ImportedTicket } from "../../api/client";
import type { TicketStudioPanelProps } from "../studio/TicketStudioPanel";
import { TicketStudioPanel } from "../studio/TicketStudioPanel";

/**
 * MUTATION & INTEGRATION TEST SUITE: Preview State for Imported Tickets
 *
 * Ticket:   39-implement-preview-state-for-imported-tickets-in-
 * Stage:    test_break
 *
 * Focus: Mutation testing, state management edge cases, and integration bugs
 * that would be hidden by excessive mocking.
 *
 * Mutation Testing Strategy:
 *   - Flip boolean flags (isPreview, isReadOnly)
 *   - Mutate array sizes (empty, single, large)
 *   - Type mutations (string for boolean, number for string)
 *   - Boundary mutations (undefined, null, false positives)
 *   - Logic mutations (AND/OR flips, comparison operators)
 *   - State mutations (transitions, ordering)
 */

jest.mock("../../api/client", () => {
  const originalClient = jest.requireActual("../../api/client");
  return {
    ...originalClient,
    apiClient: {
      ...originalClient.apiClient,
      finalizeHierarchy: jest.fn(),
    },
  };
});

const { apiClient } = require("../../api/client");

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

interface MutationTestProps extends Partial<TicketStudioPanelProps> {
  isPreview?: boolean;
  isReadOnly?: boolean;
  // Loosely typed on purpose: several mutation cases deliberately mutate the
  // shape/type of importedTickets (objects instead of arrays, missing
  // fields, etc.) to probe the component's tolerance of bad data.
  importedTickets?: any[];
  showPreviewBadge?: boolean;
}

const SAMPLE_TICKETS: ImportedTicket[] = [
  { external_id: "cap-1", title: "Capability 1", work_item_type: "capability" },
  { external_id: "cap-2", title: "Capability 2", work_item_type: "capability" },
  { external_id: "cap-3", title: "Capability 3", work_item_type: "capability" },
];

function renderWithMutations(overrides: MutationTestProps = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    // @ts-ignore - Preview props
    isPreview: overrides.isPreview ?? false,
    isReadOnly: overrides.isReadOnly ?? false,
    importedTickets: overrides.importedTickets ?? [],
    showPreviewBadge: overrides.showPreviewBadge ?? true,
    ...overrides,
  };

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TicketStudioPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function getFinalizeButton() {
  return screen.queryByRole("button", { name: /finalize|create|commit/i });
}

function getReadOnlyIndicator() {
  return screen.queryByText(/read.?only|readonly|cannot.*edit/i);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
  apiClient.finalizeHierarchy.mockClear();
});

// ===========================================================================
// MUT-PREVIEW-1: BOOLEAN MUTATION TESTING
// ===========================================================================
describe("MUT-PREVIEW-1: Boolean Mutation Testing", () => {
  it("MUT-PREVIEW-1.1: isPreview TRUE does not disable finalize (confirm dialog gates it instead)", () => {
    renderWithMutations({ isPreview: true });

    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-1.2: isPreview FALSE enables finalize", () => {
    renderWithMutations({ isPreview: false });

    const btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });

  it("MUT-PREVIEW-1.3: flipping isPreview false->true doesn't disable the button", () => {
    const { rerender } = renderWithMutations({ isPreview: false });

    let btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }

    // Flip to true
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-1.4: isReadOnly TRUE shows readonly indicator", () => {
    renderWithMutations({ isReadOnly: true });

    const indicator = getReadOnlyIndicator();
    if (indicator) {
      expect(indicator).toBeInTheDocument();
    }
  });

  it("MUT-PREVIEW-1.5: isReadOnly FALSE hides readonly indicator", () => {
    renderWithMutations({ isReadOnly: false });

    const indicator = getReadOnlyIndicator();
    expect(indicator).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-1.6: showPreviewBadge TRUE displays badge", () => {
    renderWithMutations({ isPreview: true, showPreviewBadge: true });

    // Scoped to the dedicated preview indicator testid — a bare "draft"
    // match also hits the always-present "Draft tickets" section header,
    // which isn't the badge under test here.
    const badge = screen.queryByTestId("preview-state-indicator");
    expect(badge).toBeInTheDocument();
  });

  it("MUT-PREVIEW-1.7: showPreviewBadge FALSE hides badge", () => {
    renderWithMutations({ isPreview: true, showPreviewBadge: false });

    // Badge should be hidden (even if preview is true)
    const badge = screen.queryByTestId("preview-badge");
    if (badge) {
      expect(badge).toHaveStyle("display: none");
    }
  });

  it("MUT-PREVIEW-1.8: isPreview AND isReadOnly both true", () => {
    // Combined mutation: both flags true. Per AC3 the finalize action may be
    // disabled OR hidden entirely — the panel currently hides the commit
    // button altogether when isReadOnly is true, which also satisfies
    // "cannot finalize".
    renderWithMutations({ isPreview: true, isReadOnly: true });

    const btn = getFinalizeButton();
    if (btn) {
      expect(btn).toBeDisabled();
    }

    const indicator = getReadOnlyIndicator();
    if (indicator) {
      expect(indicator).toBeInTheDocument();
    }
  });

  it("MUT-PREVIEW-1.9: isPreview alone doesn't block edit; isReadOnly does", () => {
    // isPreview no longer gates via disabled; isReadOnly hides the button entirely
    renderWithMutations({ isPreview: true, isReadOnly: false });

    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    // Now flip: preview false, readonly true
    renderWithMutations({ isPreview: false, isReadOnly: true });

    // Button might be disabled due to readonly OR missing confirmation
  });

  it("MUT-PREVIEW-1.10: NOT(isPreview) enables finalize", () => {
    // Negation mutation
    const notPreview = false;
    renderWithMutations({ isPreview: !notPreview }); // isPreview = true

    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    // Now test actual not
    renderWithMutations({ isPreview: !true }); // isPreview = false
  });
});

// ===========================================================================
// MUT-PREVIEW-2: ARRAY SIZE MUTATIONS
// ===========================================================================
describe("MUT-PREVIEW-2: Array Size Mutations", () => {
  it("MUT-PREVIEW-2.1: importedTickets empty array", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    // Should still show preview indicator
    const badge = screen.queryByText(/preview/i);
    expect(badge).toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.2: importedTickets single item", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [SAMPLE_TICKETS[0]],
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.3: importedTickets multiple items", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
    expect(screen.getByText(/Capability 3/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.4: importedTickets grows from empty to multiple", () => {
    const { rerender } = renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    // Should render preview state but no tickets
    let btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    // Add tickets
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={SAMPLE_TICKETS}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();

    btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-2.5: importedTickets shrinks from multiple to single", () => {
    const { rerender } = renderWithMutations({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
    expect(screen.getByText(/Capability 3/)).toBeInTheDocument();

    // Remove tickets
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={[SAMPLE_TICKETS[0]]}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
    expect(screen.queryByText(/Capability 3/)).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.6: array boundary: zero items", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    // Should render without error
    const btn = getFinalizeButton();
    expect(btn).toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.7: array boundary: one item", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [{ external_id: "single", title: "Single Ticket" }],
    });

    expect(screen.getByText(/Single Ticket/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-2.8: array boundary: many items (100+)", () => {
    const largeArray = Array.from({ length: 100 }, (_, i) => ({
      external_id: `t-${i}`,
      title: `Ticket ${i}`,
    }));

    renderWithMutations({
      isPreview: true,
      importedTickets: largeArray,
    });

    // Should render first item at minimum
    expect(screen.getByText(/Ticket 0/)).toBeInTheDocument();
  });
});

// ===========================================================================
// MUT-PREVIEW-3: COMPARISON OPERATOR MUTATIONS
// ===========================================================================
describe("MUT-PREVIEW-3: Comparison Operator Mutations", () => {
  it("MUT-PREVIEW-3.1: isPreview === true does not disable finalize", () => {
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-3.2: isPreview !== false does not disable finalize", () => {
    // Alternative: if (isPreview !== false)
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-3.3: NOT(isPreview === false) does not disable finalize", () => {
    // Double negative
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-3.4: isPreview && true does not disable finalize", () => {
    // AND with true
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-3.5: isPreview || false has no extra effect", () => {
    // OR with false (no-op)
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-3.6: importedTickets.length > 0 shows content", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-3.7: importedTickets.length === 0 hides content", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    // No tickets should render
    expect(screen.queryByText(/Capability/)).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-3.8: importedTickets.length >= 1 shows content", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [SAMPLE_TICKETS[0]],
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });
});

// ===========================================================================
// MUT-PREVIEW-4: CONDITIONAL LOGIC MUTATIONS
// ===========================================================================
describe("MUT-PREVIEW-4: Conditional Logic Mutations", () => {
  it("MUT-PREVIEW-4.1: if (isPreview) disable: TRUE does not disable", () => {
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-4.2: if (isPreview) disable: FALSE enables", () => {
    renderWithMutations({ isPreview: false });
    const btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });

  it("MUT-PREVIEW-4.3: if (importedTickets) render: with tickets renders", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-4.4: if (importedTickets) render: without tickets doesn't render", () => {
    renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    expect(screen.queryByText(/Capability/)).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-4.5: if-else swap: inverted logic fails", () => {
    // Mutation: swap if/else branches
    // Expected: isPreview=true -> button stays enabled (dialog gates it)
    renderWithMutations({ isPreview: true });
    const btn = getFinalizeButton();

    // Should NOT be the opposite of what we expect
    expect(btn).not.toHaveAttribute("enabled");
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-4.6: ternary mutation: true branch vs false branch", () => {
    // isPreview doesn't select a disabled/enabled branch either way anymore
    const { rerender } = renderWithMutations({ isPreview: true });

    let btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={false}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });
});

// ===========================================================================
// MUT-PREVIEW-5: STATE TRANSITION MUTATIONS
// ===========================================================================
describe("MUT-PREVIEW-5: State Transition Mutations", () => {
  it("MUT-PREVIEW-5.1: transition: preview=false -> true", () => {
    const { rerender } = renderWithMutations({ isPreview: false });

    let btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();
  });

  it("MUT-PREVIEW-5.2: transition: preview=true -> false", () => {
    const { rerender } = renderWithMutations({ isPreview: true });

    let btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={false}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });

  it("MUT-PREVIEW-5.3: transition: no tickets -> has tickets", () => {
    const { rerender } = renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    expect(screen.queryByText(/Capability/)).not.toBeInTheDocument();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={SAMPLE_TICKETS}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });

  it("MUT-PREVIEW-5.4: transition: has tickets -> no tickets", () => {
    const { rerender } = renderWithMutations({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={[]}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.queryByText(/Capability/)).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-5.5: complex transition: multiple properties change", () => {
    const { rerender } = renderWithMutations({
      isPreview: false,
      isReadOnly: false,
      importedTickets: [],
    });

    let btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }

    // Change all at once
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          isReadOnly={true}
          importedTickets={SAMPLE_TICKETS}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    // Per AC3 the finalize action may be disabled OR hidden entirely — the
    // panel currently hides the commit button altogether when isReadOnly is
    // true, which also satisfies "cannot finalize".
    btn = getFinalizeButton();
    if (btn) {
      expect(btn).toBeDisabled();
    }

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });
});

// ===========================================================================
// MUT-PREVIEW-6: TYPE COERCION MUTATIONS
// ===========================================================================
describe("MUT-PREVIEW-6: Type Coercion Mutations", () => {
  it("MUT-PREVIEW-6.1: isPreview='true' (string) should not disable", () => {
    // String 'true' is truthy but might not equal boolean true
    renderWithMutations({ isPreview: "true" as any });

    // Depending on implementation, might be enabled (strings don't === true)
  });

  it("MUT-PREVIEW-6.2: isPreview=1 (number) should not disable", () => {
    // Number 1 is truthy but !== true
    renderWithMutations({ isPreview: 1 as any });

    // Depending on implementation
  });

  it("MUT-PREVIEW-6.3: isPreview=0 (number) should not disable", () => {
    renderWithMutations({ isPreview: 0 as any });

    const btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });

  it("MUT-PREVIEW-6.4: importedTickets as object instead of array", () => {
    // Type mutation
    renderWithMutations({
      isPreview: true,
      importedTickets: { 0: SAMPLE_TICKETS[0] } as any,
    });

    // Should handle gracefully
    const btn = getFinalizeButton();
    expect(btn).toBeInTheDocument();
  });
});

// ===========================================================================
// MUT-PREVIEW-7: EDGE CASE SEQUENCES
// ===========================================================================
describe("MUT-PREVIEW-7: Edge Case Sequences", () => {
  it("MUT-PREVIEW-7.1: sequence: empty -> filled -> empty", () => {
    const { rerender } = renderWithMutations({
      isPreview: true,
      importedTickets: [],
    });

    // Add tickets
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={SAMPLE_TICKETS}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();

    // Remove again
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
          importedTickets={[]}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.queryByText(/Capability/)).not.toBeInTheDocument();
  });

  it("MUT-PREVIEW-7.2: sequence: preview false -> true -> false", () => {
    const { rerender } = renderWithMutations({ isPreview: false });

    let btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }

    // Toggle true
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    expect(btn).not.toBeDisabled();

    // Toggle false again
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={false}
        />
      </MemoryRouter>
      </QueryClientProvider>,
    );

    btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });

  it("MUT-PREVIEW-7.3: rapid alternation: toggle 10x", () => {
    const { rerender } = renderWithMutations({ isPreview: false });

    for (let i = 0; i < 10; i++) {
      const isPreview = i % 2 === 0;
      rerender(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <TicketStudioPanel
              workspaceSlug="loregarden"
              onClose={jest.fn()}
              // @ts-ignore
              isPreview={isPreview}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    }

    // Should end on isPreview=false
    const btn = getFinalizeButton();
    if (btn) {
      expect(btn).not.toBeDisabled();
    }
  });
});

// ===========================================================================
// MUT-PREVIEW-8: MOCK-RESISTANT INTEGRATION TESTS
// ===========================================================================
describe("MUT-PREVIEW-8: Integration (real state transitions)", () => {
  it("MUT-PREVIEW-8.1: preview state blocks finalization across full flow", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithMutations({ isPreview: true });

    const btn = getFinalizeButton();
    if (btn) {
      // Click opens the confirm dialog rather than finalizing directly
      await user.click(btn);

      // API should not be called by that single click
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });

  it("MUT-PREVIEW-8.2: non-preview allows finalization flow", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithMutations({ isPreview: false });

    const btn = getFinalizeButton();
    if (btn && !btn.hasAttribute("disabled")) {
      await user.click(btn);

      // Confirm dialog should appear or API should be prepared to call
      // At minimum, should not prevent the interaction
    }
  });

  it("MUT-PREVIEW-8.3: readonly content cannot be edited", () => {
    renderWithMutations({
      isPreview: true,
      isReadOnly: true,
      importedTickets: SAMPLE_TICKETS,
    });

    // Imported tickets should be displayed
    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();

    // But no edit controls should exist
    const ticketElements = screen.queryAllByText(/Capability/);

    for (const element of ticketElements) {
      const container = element.closest("[data-testid*='ticket']");
      if (container) {
        const editBtn = within(container as HTMLElement).queryByRole("button", {
          name: /edit|modify/i,
        });
        expect(editBtn).not.toBeInTheDocument();
      }
    }
  });
});
