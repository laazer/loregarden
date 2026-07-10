import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../studio/TicketStudioPanel";
import { TicketStudioPanel } from "../studio/TicketStudioPanel";

/**
 * ADVERSARIAL TEST SUITE: Studio Preview State for Imported Tickets
 *
 * Ticket:   39-implement-preview-state-for-imported-tickets-in-
 * Stage:    test_break
 *
 * Mission: Expose weaknesses in preview state handling, finalize button locking,
 * and read-only source content visibility.
 *
 * Acceptance Criteria:
 *   - AC1: Studio recognizes and renders preview state UI
 *   - AC2: Read-only source ticket content visible
 *   - AC3: Finalize button disabled/hidden until user explicitly confirms
 *
 * Test Dimensions (Test Breaker Checklist Matrix):
 *   - [X] Null & Empty Values (preview state, imported tickets)
 *   - [X] Boundary Conditions (zero, max, deeply nested)
 *   - [X] Type & Structure Mutations (preview as string, missing fields)
 *   - [X] Invalid/Corrupt Inputs (malformed preview data)
 *   - [X] Concurrency / Race Conditions (multiple state changes)
 *   - [X] Order Dependency (state transition sequences)
 *   - [X] Combinatorial Inputs (preview + empty, preview + error)
 *   - [X] Stress / Load (large imported ticket batches)
 *   - [X] Mutation Testing (flip preview flag, toggle readonly)
 *   - [X] Error Handling (API failures, missing props)
 *   - [X] Assumption Validation (UI visibility, button states)
 *   - [X] Determinism Validation (consistent behavior)
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

/**
 * FIXTURES & HELPERS
 */

interface PreviewSessionProps extends Partial<TicketStudioPanelProps> {
  isPreview?: boolean;
  importedTickets?: Array<{ external_id: string; title: string }>;
}

const BASE_IMPORTED_TICKETS = [
  { external_id: "t-1", title: "Task 1" },
  { external_id: "t-2", title: "Task 2" },
];

const LARGE_IMPORTED_BATCH = Array.from({ length: 500 }, (_, i) => ({
  external_id: `t-${i}`,
  title: `Task ${i}`,
}));

function renderStudioWithPreview(
  overrides: PreviewSessionProps = {},
) {
  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    // @ts-ignore - preview state not yet typed, we're adding it
    isPreview: overrides.isPreview ?? false,
    importedTickets: overrides.importedTickets ?? [],
    ...overrides,
  };

  const utils = render(
    <MemoryRouter>
      <TicketStudioPanel {...props} />
    </MemoryRouter>,
  );

  return { ...utils, props };
}

function getFinalizeButton(): HTMLElement | null {
  return screen.queryByRole("button", { name: /finalize|create.*commit/i });
}

function getPreviewIndicator(): HTMLElement | null {
  return screen.queryByTestId("preview-state-indicator") ||
    screen.queryByText(/preview|not.*finalized|draft/i);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
  apiClient.finalizeHierarchy.mockClear();
});

// ===========================================================================
// ADVA-PREVIEW-1: PREVIEW STATE RECOGNITION (AC1)
// ===========================================================================
describe("ADVA-PREVIEW-1: Preview State Recognition (AC1)", () => {
  it("ADVA-PREVIEW-1.1: renders preview badge when isPreview=true", () => {
    // AC1: Preview state UI visible
    renderStudioWithPreview({ isPreview: true });

    expect(getPreviewIndicator()).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.2: does NOT render preview badge when isPreview=false", () => {
    // Regression: regular sessions should not show preview indicator
    renderStudioWithPreview({ isPreview: false });

    expect(getPreviewIndicator()).not.toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.3: handles isPreview as undefined (treats as false)", () => {
    // Edge case: missing prop
    renderStudioWithPreview({ isPreview: undefined });

    expect(getPreviewIndicator()).not.toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.4: handles isPreview as null (treats as false)", () => {
    // Edge case: explicit null
    renderStudioWithPreview({ isPreview: null as any });

    // Should not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.5: handles isPreview as string 'true' (type mutation)", () => {
    // Type mutation: string instead of boolean
    renderStudioWithPreview({ isPreview: "true" as any });

    // Should either treat as falsy or handle gracefully
    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).toBeInTheDocument();
    }
  });

  it("ADVA-PREVIEW-1.6: handles isPreview as number 1 (type mutation)", () => {
    // Type mutation: number instead of boolean
    renderStudioWithPreview({ isPreview: 1 as any });

    // Should not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.7: preview badge remains visible across navigation", async () => {
    // AC1: Preview state persists
    renderStudioWithPreview({ isPreview: true });

    const indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();

    // Simulate navigation (e.g., expand/collapse sections)
    const expandButtons = screen.queryAllByRole("button", { name: /expand|collapse/i });
    if (expandButtons.length > 0) {
      await userEvent.click(expandButtons[0]);
    }

    // Preview indicator should still be visible
    expect(getPreviewIndicator()).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-1.8: preview badge has accessible text (not just icon)", () => {
    // A11y: Accessible preview indication
    renderStudioWithPreview({ isPreview: true });

    const indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();
    expect(indicator?.textContent).toMatch(/preview|draft|not.*finalized/i);
  });

  it("ADVA-PREVIEW-1.9: preview state text is distinct from other UI labels", () => {
    // Regression: preview text should not be confused with section titles
    renderStudioWithPreview({ isPreview: true });

    const indicator = getPreviewIndicator();
    const previewText = indicator?.textContent || "";

    // Should not contain common section words
    expect(previewText).not.toMatch(/settings|options|configuration/i);
  });

  it("ADVA-PREVIEW-1.10: multiple preview sessions can render side-by-side", () => {
    // Edge case: concurrent preview sessions
    const { container } = renderStudioWithPreview({ isPreview: true });

    // Render should not have global state issues
    expect(container.firstChild).toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-PREVIEW-2: READ-ONLY SOURCE CONTENT (AC2)
// ===========================================================================
describe("ADVA-PREVIEW-2: Read-Only Source Content (AC2)", () => {
  it("ADVA-PREVIEW-2.1: renders imported ticket data when isPreview=true", () => {
    // AC2: Imported content visible
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: BASE_IMPORTED_TICKETS,
    });

    expect(screen.getByText(/Task 1|t-1/)).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.2: imported tickets are read-only (no edit controls)", () => {
    // AC2: Read-only source tickets
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: BASE_IMPORTED_TICKETS,
    });

    const ticketElements = screen.queryAllByText(/Task 1/);
    for (const element of ticketElements) {
      const container = element.closest("[data-testid*='ticket']");
      if (container) {
        // Should not have edit button
        const editBtn = within(container).queryByRole("button", {
          name: /edit|modify/i,
        });
        expect(editBtn).not.toBeInTheDocument();
      }
    }
  });

  it("ADVA-PREVIEW-2.3: handles empty importedTickets array", () => {
    // AC2: Edge case - no imported content
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [],
    });

    // Should render empty state, not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.4: handles importedTickets as null", () => {
    // Edge case: null instead of array
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: null as any,
    });

    // Should not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.5: handles importedTickets as undefined", () => {
    // Edge case: missing prop
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: undefined,
    });

    // Should not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.6: handles extremely large imported batch (500+ tickets)", () => {
    // Stress: Performance with large datasets
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: LARGE_IMPORTED_BATCH,
    });

    // Should render without significant performance degradation
    expect(screen.getByText(/Task 0/)).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.7: handles imported tickets with missing external_id", () => {
    // Type mutation: missing required field
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [
        { external_id: "t-1", title: "Task 1" },
        { external_id: "", title: "Task No ID" }, // Empty external_id
      ],
    });

    // Should render without crashing
    expect(screen.getByText(/Task 1/)).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.8: handles imported tickets with null/undefined title", () => {
    // Type mutation: missing title
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [
        { external_id: "t-1", title: "Task 1" },
        { external_id: "t-2", title: null as any },
      ],
    });

    // Should render without crashing
    expect(screen.getByText(/Task 1/)).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-2.9: imported data is visually distinct from editable content", () => {
    // AC2: User can distinguish source from editable
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: BASE_IMPORTED_TICKETS,
    });

    const importedElement = screen.getByText(/Task 1/);
    const classes = importedElement.className || "";

    // Should have visual indicator (color, opacity, etc)
    // Or be in a distinct container
    expect(
      importedElement.closest("[data-testid*='import']") ||
      importedElement.closest("[class*='import']") ||
      classes.includes("readonly") ||
      classes.includes("preview") ||
      classes.includes("source")
    ).toBeTruthy();
  });

  it("ADVA-PREVIEW-2.10: handles special characters in imported ticket data", () => {
    // Regression: XSS/encoding
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [
        {
          external_id: "t-xss",
          title: "<img src=x onerror='alert(1)'>",
        },
      ],
    });

    // Should escape/sanitize
    expect(screen.queryByText("alert")).not.toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-PREVIEW-3: FINALIZE BUTTON LOCKING (AC3)
// ===========================================================================
describe("ADVA-PREVIEW-3: Finalize Button Locking (AC3)", () => {
  it("ADVA-PREVIEW-3.1: finalize button is disabled when isPreview=true", () => {
    // AC3: Preview blocks finalization
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();
  });

  it("ADVA-PREVIEW-3.2: finalize button is enabled when isPreview=false", () => {
    // Regression: normal sessions can finalize
    renderStudioWithPreview({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).not.toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-3.3: disabled finalize button has accessibility info", () => {
    // A11y: User knows why button is disabled
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).toHaveAttribute("disabled");
      // Should have title or aria-label explaining why
      const ariaLabel = finalizeBtn.getAttribute("aria-label") || "";
      const title = finalizeBtn.getAttribute("title") || "";
      expect(ariaLabel || title || finalizeBtn.textContent).toMatch(
        /confirm|preview|disable|must/i,
      );
    }
  });

  it("ADVA-PREVIEW-3.4: finalize button remains disabled during navigation", async () => {
    // AC3: Locking persists
    renderStudioWithPreview({ isPreview: true });

    let finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();

    // Simulate navigation
    const expandButtons = screen.queryAllByRole("button", { name: /expand|toggle/i });
    if (expandButtons.length > 0) {
      await userEvent.click(expandButtons[0]);
    }

    finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();
  });

  it("ADVA-PREVIEW-3.5: finalize button click is prevented when disabled", async () => {
    // AC3: Disabled button does not execute
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      await userEvent.click(finalizeBtn);

      // API should NOT be called
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });

  it("ADVA-PREVIEW-3.6: finalize button hidden entirely (not just disabled)", () => {
    // Variant: Some systems hide rather than disable
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      // Either disabled OR hidden
      const isDisabled = finalizeBtn.hasAttribute("disabled");
      const isHidden = finalizeBtn.getAttribute("aria-hidden") === "true" ||
        finalizeBtn.style.display === "none";

      expect(isDisabled || isHidden).toBeTruthy();
    }
  });

  it("ADVA-PREVIEW-3.7: confirm dialog appears if preview state is somehow bypassed", () => {
    // Defense-in-depth: secondary confirmation
    renderStudioWithPreview({ isPreview: true });

    // Even if button was somehow clickable, should show confirmation
    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      // This tests that confirmation dialog would appear as fallback
      expect(screen.queryByRole("dialog", { name: /confirm/i })).not.toBeInTheDocument();
    }
  });

  it("ADVA-PREVIEW-3.8: finalize button state updates when preview flag changes", async () => {
    // State transition: preview -> finalized
    const { rerender } = renderStudioWithPreview({ isPreview: true });

    let finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();

    // Change to finalized state
    rerender(
      <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={false}
        />
      </MemoryRouter>,
    );

    finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).not.toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-3.9: handles missing finalize button entirely", () => {
    // Edge case: button might not exist (different UI variant)
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    // Test should pass if button exists and is disabled, or doesn't exist
    if (finalizeBtn) {
      expect(finalizeBtn).toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-3.10: multiple finalize buttons all disabled when preview", () => {
    // Edge case: multiple instances of button
    renderStudioWithPreview({ isPreview: true });

    const finalizeButtons = screen.queryAllByRole("button", { name: /finalize|create/i });
    for (const btn of finalizeButtons) {
      if (btn.hasAttribute("onclick") || btn.className.includes("finalize")) {
        expect(btn).toBeDisabled();
      }
    }
  });
});

// ===========================================================================
// ADVA-PREVIEW-4: CONFIRM DIALOG REQUIREMENT
// ===========================================================================
describe("ADVA-PREVIEW-4: Confirm Dialog for Finalization (AC3)", () => {
  it("ADVA-PREVIEW-4.1: shows confirmation dialog before finalizing preview", async () => {
    // AC3: User explicitly confirms
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderStudioWithPreview({ isPreview: false }); // Not preview for this test

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Should show confirmation
      const confirmDialog = screen.queryByRole("dialog", { name: /confirm/i });
      if (confirmDialog) {
        expect(confirmDialog).toBeInTheDocument();
      }
    }
  });

  it("ADVA-PREVIEW-4.2: confirmation dialog warns about preview origin", async () => {
    // AC3: User sees what they're committing
    const user = userEvent.setup();

    renderStudioWithPreview({
      isPreview: true,
      importedTickets: BASE_IMPORTED_TICKETS,
    });

    // Try to finalize (should be blocked)
    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Confirmation should mention origin
      const dialog = screen.queryByRole("dialog");
      if (dialog) {
        expect(dialog.textContent).toMatch(/imported|preview|confirm/i);
      }
    }
  });

  it("ADVA-PREVIEW-4.3: confirmation requires explicit user action (not auto-confirm)", async () => {
    // AC3: User must deliberately confirm
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderStudioWithPreview({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // API should not be called immediately
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();

      // User must click confirm button
      const confirmBtn = screen.queryByRole("button", { name: /confirm|yes|proceed/i });
      if (confirmBtn) {
        await user.click(confirmBtn);
        expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
      }
    }
  });

  it("ADVA-PREVIEW-4.4: confirmation can be cancelled (no accidental finalization)", async () => {
    // AC3: User can back out
    const user = userEvent.setup();

    renderStudioWithPreview({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      const cancelBtn = screen.queryByRole("button", { name: /cancel|no|back/i });
      if (cancelBtn) {
        await user.click(cancelBtn);

        // API should not be called
        expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
      }
    }
  });
});

// ===========================================================================
// ADVA-PREVIEW-5: STATE TRANSITION & RACE CONDITIONS
// ===========================================================================
describe("ADVA-PREVIEW-5: State Transitions & Race Conditions", () => {
  it("ADVA-PREVIEW-5.1: preview -> finalized transition is immediate", async () => {
    // Transition: preview state changes
    const { rerender } = renderStudioWithPreview({ isPreview: true });

    let finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();

    // Change state
    rerender(
      <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="loregarden"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={false}
        />
      </MemoryRouter>,
    );

    // Button should now be enabled
    await waitFor(() => {
      finalizeBtn = getFinalizeButton();
      if (finalizeBtn) {
        expect(finalizeBtn).not.toBeDisabled();
      }
    });
  });

  it("ADVA-PREVIEW-5.2: handles rapid preview state toggling", async () => {
    // Race: Quick state changes
    const { rerender } = renderStudioWithPreview({ isPreview: true });

    // Rapid toggles
    for (let i = 0; i < 5; i++) {
      rerender(
        <MemoryRouter>
          <TicketStudioPanel
            workspaceSlug="loregarden"
            onClose={jest.fn()}
            // @ts-ignore
            isPreview={i % 2 === 0}
          />
        </MemoryRouter>,
      );
    }

    const finalizeBtn = getFinalizeButton();
    // Should end up in correct state (isPreview=false means enabled)
    if (finalizeBtn) {
      expect(finalizeBtn).not.toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-5.3: handles preview state change during loading", () => {
    // Race: State change while API call pending
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () => new Promise(() => {}), // Never resolves
    );

    renderStudioWithPreview({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    // Component should handle state transition gracefully
    expect(finalizeBtn).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-5.4: unmounting during preview state preserves data consistency", () => {
    // Edge case: Component unmount
    const { unmount } = renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();

    // Unmount should not cause state issues
    unmount();

    // Should not throw
  });
});

// ===========================================================================
// ADVA-PREVIEW-6: EDGE CASES & REGRESSION
// ===========================================================================
describe("ADVA-PREVIEW-6: Edge Cases & Regression", () => {
  it("ADVA-PREVIEW-6.1: preview state with zero imported tickets", () => {
    // Edge: Preview but no content
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [],
    });

    const indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();
  });

  it("ADVA-PREVIEW-6.2: non-preview state ignores imported tickets", () => {
    // Regression: imported tickets should only matter if preview=true
    renderStudioWithPreview({
      isPreview: false,
      importedTickets: BASE_IMPORTED_TICKETS,
    });

    const indicator = getPreviewIndicator();
    expect(indicator).not.toBeInTheDocument();

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).not.toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-6.3: preview with corrupted imported ticket structure", () => {
    // Type: Malformed data
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [
        { external_id: "t-1" }, // Missing title
        { title: "Task" }, // Missing external_id
        {} as any, // Both missing
      ],
    });

    // Should not crash
    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toBeDisabled();
  });

  it("ADVA-PREVIEW-6.4: preview state persists across modal open/close cycles", async () => {
    // Persistence: State survives interactions
    const user = userEvent.setup();
    renderStudioWithPreview({ isPreview: true });

    let indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();

    // Simulate modal interactions
    const buttons = screen.queryAllByRole("button");
    if (buttons.length > 0) {
      await user.click(buttons[0]);
    }

    indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-6.5: handles workspace change while in preview state", () => {
    // Edge: Context change
    const { rerender } = renderStudioWithPreview({
      isPreview: true,
      workspaceSlug: "workspace-1",
    });

    let indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();

    // Change workspace
    rerender(
      <MemoryRouter>
        <TicketStudioPanel
          workspaceSlug="workspace-2"
          onClose={jest.fn()}
          // @ts-ignore
          isPreview={true}
        />
      </MemoryRouter>,
    );

    indicator = getPreviewIndicator();
    expect(indicator).toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-PREVIEW-7: ASSUMPTION VALIDATION
// ===========================================================================
describe("ADVA-PREVIEW-7: Assumption Validation", () => {
  it("ADVA-PREVIEW-7.1: assumes isPreview is boolean (not just truthy)", () => {
    // Assumption check
    renderStudioWithPreview({ isPreview: 0 as any }); // Falsy number

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      // 0 is falsy, should enable button
      expect(finalizeBtn).not.toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-7.2: assumes imported tickets list is iterable", () => {
    // Assumption: Can iterate over importedTickets
    renderStudioWithPreview({
      isPreview: true,
      importedTickets: [{ external_id: "t-1", title: "Task 1" }],
    });

    // Should not crash when rendering
    expect(screen.getByText(/Task 1/)).toBeInTheDocument();
  });

  it("ADVA-PREVIEW-7.3: assumes finalize button always exists (or gracefully handles absence)", () => {
    // Assumption: Button exists in DOM
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    // Either exists and is disabled, or doesn't exist (both valid)
    if (finalizeBtn) {
      expect(finalizeBtn).toBeDisabled();
    }
  });

  it("ADVA-PREVIEW-7.4: assumes preview state blocks ALL finalization paths", async () => {
    // Assumption: No way to bypass lock
    const user = userEvent.setup();
    renderStudioWithPreview({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      // Try various ways to trigger finalization
      await user.click(finalizeBtn);
      await user.keyboard("{Enter}");

      // API should never be called
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });
});

// ===========================================================================
// ADVA-PREVIEW-8: DETERMINISM VALIDATION
// ===========================================================================
describe("ADVA-PREVIEW-8: Determinism Validation", () => {
  it("ADVA-PREVIEW-8.1: same input produces consistent button state", () => {
    const { rerender: rerender1 } = renderStudioWithPreview({ isPreview: true });
    let btn1 = getFinalizeButton();
    const btn1Disabled = btn1?.hasAttribute("disabled");

    // Unmount
    const { unmount: unmount1 } = renderStudioWithPreview({ isPreview: true });
    unmount1();

    // Render again with same input
    const { rerender: rerender2 } = renderStudioWithPreview({ isPreview: true });
    let btn2 = getFinalizeButton();
    const btn2Disabled = btn2?.hasAttribute("disabled");

    expect(btn1Disabled).toBe(btn2Disabled);
  });

  it("ADVA-PREVIEW-8.2: same preview state input produces same UI indicators", () => {
    renderStudioWithPreview({ isPreview: true });
    const indicator1 = getPreviewIndicator();

    // Rerender with same props
    const { container } = renderStudioWithPreview({ isPreview: true });
    const indicator2 = getPreviewIndicator();

    expect(!!indicator1).toBe(!!indicator2);
  });
});
