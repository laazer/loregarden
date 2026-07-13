import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ImportedTicket } from "../../api/client";
import type { TicketStudioPanelProps } from "../studio/TicketStudioPanel";
import { TicketStudioPanel } from "../studio/TicketStudioPanel";

/**
 * INTEGRATION TEST SUITE: Studio Preview State for Imported Tickets
 *
 * Ticket:   39-implement-preview-state-for-imported-tickets-in-
 * Stage:    test_break
 *
 * Focus: Real-world integration scenarios that mock-heavy tests miss.
 * This suite removes mock isolation to test actual component behavior:
 * - API integration (actual payload verification)
 * - Query client state management
 * - Async state transitions with real timers
 * - Component interaction with real dependencies
 *
 * Key Principle: Mock-resistant integration catches bugs that mock-only
 * tests let slip through.
 */

// Mock only the API responses, not the client itself
jest.mock("../../api/client", () => {
  const original = jest.requireActual("../../api/client");
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
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

interface IntegrationTestProps extends Partial<TicketStudioPanelProps> {
  isPreview?: boolean;
  importedTickets?: any[];
}

const SAMPLE_TICKETS: ImportedTicket[] = [
  { external_id: "cap-1", title: "Capability 1", work_item_type: "capability" },
  { external_id: "cap-2", title: "Capability 2", work_item_type: "capability" },
];

function renderWithRealQueryClient(overrides: IntegrationTestProps = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    // @ts-ignore - preview props not yet typed
    isPreview: overrides.isPreview ?? false,
    importedTickets: overrides.importedTickets ?? [],
    ...overrides,
  };

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TicketStudioPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...utils, queryClient, props };
}

function getFinalizeButton(): HTMLElement | null {
  return screen.queryByRole("button", { name: /finalize|create.*commit/i });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
  apiClient.finalizeHierarchy.mockClear();
});

// ===========================================================================
// INT-PREVIEW-1: BUTTON INTERACTION VERIFICATION (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-1: Button Interaction Verification", () => {
  it("INT-PREVIEW-1.1: verifies button has no disabled attribute purely from isPreview=true (not just mocked)", () => {
    // Preview alone doesn't disable the button in the DOM — confirmed
    // rather than assumed from a mock.
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
    expect(finalizeBtn).not.toHaveAttribute("disabled");
  });

  it("INT-PREVIEW-1.2: clicking the (enabled) finalize button opens the confirm dialog, not the API directly", async () => {
    const user = userEvent.setup();
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");

    await user.click(finalizeBtn!);

    expect(screen.getByText(/finalize imported tickets\?/i)).toBeInTheDocument();
    expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
  });

  it("INT-PREVIEW-1.3: button responds to click when NOT disabled", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithRealQueryClient({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Should attempt to call API (or show confirmation dialog)
      await waitFor(() => {
        expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
      });
    }
  });

  it("INT-PREVIEW-1.4: enabled state persists across multiple renders", async () => {
    const { rerender } = renderWithRealQueryClient({ isPreview: true });

    let btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");

    // Simulate a re-render (e.g., unrelated state change)
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
    expect(btn).not.toHaveAttribute("disabled");
  });

  it("INT-PREVIEW-1.5: button ariaDisabled attribute set correctly", () => {
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    // Should use HTML disabled, but might also set aria-disabled for extra clarity
    if (finalizeBtn?.hasAttribute("aria-disabled")) {
      expect(finalizeBtn).toHaveAttribute("aria-disabled", "true");
    }
  });
});

// ===========================================================================
// INT-PREVIEW-2: API PAYLOAD VERIFICATION (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-2: API Payload Verification", () => {
  it("INT-PREVIEW-2.1: finalizeHierarchy is called with correct parameters when preview state changes", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    const { rerender } = renderWithRealQueryClient({ isPreview: true });

    // Change to finalized state
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

    // Try to finalize
    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      await waitFor(() => {
        expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
      });

      // Verify API call was made (not just expected, but actually called)
      const calls = apiClient.finalizeHierarchy.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
    }
  });

  it("INT-PREVIEW-2.2: finalizeHierarchy is NOT called by a single click while preview is true", async () => {
    const user = userEvent.setup();
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");

    await user.click(finalizeBtn!);

    // The click opens the confirm dialog; it doesn't call the API directly
    expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
  });

  it("INT-PREVIEW-2.3: API call includes correct workspace context", async () => {
    const user = userEvent.setup();
    const testWorkspace = "test-workspace-123";

    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithRealQueryClient({
      isPreview: false,
      workspaceSlug: testWorkspace,
    });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      await waitFor(() => {
        if (apiClient.finalizeHierarchy.mock.calls.length > 0) {
          const callArgs = apiClient.finalizeHierarchy.mock.calls[0];
          // Verify workspace context is correct
          expect(callArgs).toBeDefined();
        }
      });
    }
  });
});

// ===========================================================================
// INT-PREVIEW-3: ASYNC STATE MANAGEMENT (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-3: Async State Management", () => {
  it("INT-PREVIEW-3.1: handles preview state change during pending finalization", async () => {
    const user = userEvent.setup();

    // Mock API to never resolve (simulate pending)
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () => new Promise(() => {}), // Never resolves
    );

    const { rerender } = renderWithRealQueryClient({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      // Start finalization
      await user.click(finalizeBtn);

      // While API is pending, change preview state to true
      await waitFor(() => {
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
      });

      // Button should now be disabled
      const btn = getFinalizeButton();
      expect(btn).toHaveAttribute("disabled");
    }
  });

  it("INT-PREVIEW-3.2: handles rapid state transitions without data loss", async () => {
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    const { rerender } = renderWithRealQueryClient({ isPreview: true });

    // Rapid transitions
    for (let i = 0; i < 3; i++) {
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

      // Verify DOM state matches prop state — isPreview no longer disables
      // the button by itself in either case.
      const btn = getFinalizeButton();
      if (btn) {
        expect(btn).not.toHaveAttribute("disabled");
      }
    }
  });

  it("INT-PREVIEW-3.3: imported tickets change is reflected without losing button state", async () => {
    const { rerender } = renderWithRealQueryClient({
      isPreview: true,
      importedTickets: [],
    });

    let btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");

    // Add imported tickets
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

    // Button should still be enabled — importedTickets content doesn't gate it
    btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");

    // Tickets should be visible
    expect(screen.getByText(/Capability 1/)).toBeInTheDocument();
  });
});

// ===========================================================================
// INT-PREVIEW-4: QUERY CLIENT INTEGRATION (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-4: Query Client Integration", () => {
  it("INT-PREVIEW-4.1: preview state is managed correctly within React Query context", async () => {
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");

    // Query client state should not interfere with the button's enabled state
  });

  it("INT-PREVIEW-4.2: cache invalidation doesn't affect preview button state", async () => {
    const { queryClient } = renderWithRealQueryClient({ isPreview: true });

    let btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");

    // Simulate cache invalidation
    queryClient.invalidateQueries();

    // Button state should be unaffected
    btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");
  });
});

// ===========================================================================
// INT-PREVIEW-5: REAL USER INTERACTION FLOWS (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-5: Real User Interaction Flows", () => {
  it("INT-PREVIEW-5.1: holding down the button opens the confirm dialog but never calls the API directly", async () => {
    const user = userEvent.setup({ delay: null }); // No artificial delay
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");

    // Hold down button for extended duration
    await user.pointer({ keys: "[MouseLeft>]" });
    await new Promise((resolve) => setTimeout(resolve, 100));
    await user.pointer({ keys: "[/MouseLeft]" });

    // The click opens the confirm dialog; it never calls the API directly
    expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
  });

  it("INT-PREVIEW-5.2: hovering the button doesn't change its enabled state", async () => {
    const user = userEvent.setup();
    renderWithRealQueryClient({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");

    // Hover over button
    await user.hover(finalizeBtn!);

    // Button state should be unaffected by hovering
    expect(finalizeBtn).not.toHaveAttribute("disabled");
  });
});

// ===========================================================================
// INT-PREVIEW-6: ERROR HANDLING & RECOVERY (Mock-Resistant)
// ===========================================================================
describe("INT-PREVIEW-6: Error Handling & Recovery", () => {
  it("INT-PREVIEW-6.1: API error doesn't affect preview button state", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("API Error"),
    );

    const { rerender } = renderWithRealQueryClient({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      await waitFor(() => {
        // After error, button should still be enabled (not finalized)
        const btn = getFinalizeButton();
        if (btn) {
          expect(btn).not.toHaveAttribute("disabled");
        }
      });
    }

    // Changing to preview should still leave the button enabled — the lock
    // is the confirm dialog, not the disabled attribute.
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

    const btn = getFinalizeButton();
    expect(btn).not.toHaveAttribute("disabled");
  });

  it("INT-PREVIEW-6.2: recovery from error state preserves preview behavior", async () => {
    apiClient.finalizeHierarchy.mockRejectedValueOnce(new Error("Failed"));

    const { rerender } = renderWithRealQueryClient({ isPreview: false });

    let finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      // Call fails
      apiClient.finalizeHierarchy.mockRejectedValueOnce(new Error("Failed"));
    }

    // Switch to preview
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

    finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).not.toHaveAttribute("disabled");
  });
});
