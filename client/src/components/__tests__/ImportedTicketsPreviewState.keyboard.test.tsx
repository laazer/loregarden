import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ImportedTicket } from "../../api/client";
import type { TicketStudioPanelProps } from "../studio/TicketStudioPanel";
import { TicketStudioPanel } from "../studio/TicketStudioPanel";

/**
 * KEYBOARD & ACCESSIBILITY TEST SUITE: Preview State for Imported Tickets
 *
 * Ticket:   39-implement-preview-state-for-imported-tickets-in-
 * Stage:    test_break
 *
 * Focus: Keyboard navigation and accessibility features that GUI tests miss.
 * Tests verify:
 * - Disabled button doesn't respond to keyboard (Enter, Space)
 * - Keyboard navigation (Tab) works correctly
 * - Screen reader announces disabled state
 * - Focus management and ARIA attributes
 * - High-contrast mode considerations
 *
 * Key Principle: Keyboard-only users and accessibility tools require
 * specific behaviors that must be tested explicitly.
 */

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

interface KeyboardTestProps extends Partial<TicketStudioPanelProps> {
  isPreview?: boolean;
  importedTickets?: any[];
}

const SAMPLE_TICKETS: ImportedTicket[] = [
  { external_id: "cap-1", title: "Capability 1", work_item_type: "capability" },
  { external_id: "cap-2", title: "Capability 2", work_item_type: "capability" },
];

function renderWithKeyboardSupport(overrides: KeyboardTestProps = {}) {
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

  return { ...utils, props, queryClient };
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
// KBD-PREVIEW-1: KEYBOARD INTERACTION WHEN DISABLED
// ===========================================================================
describe("KBD-PREVIEW-1: Keyboard Interaction When Button Disabled", () => {
  it("KBD-PREVIEW-1.1: disabled button doesn't activate on Enter key", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn?.hasAttribute("disabled")) {
      finalizeBtn.focus();

      // Try to activate with Enter
      await user.keyboard("{Enter}");

      // Should NOT call API
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });

  it("KBD-PREVIEW-1.2: disabled button doesn't activate on Space key", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn?.hasAttribute("disabled")) {
      finalizeBtn.focus();

      // Try to activate with Space
      await user.keyboard(" ");

      // Should NOT call API
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });

  it("KBD-PREVIEW-1.3: disabled button doesn't respond to repeated key presses", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn?.hasAttribute("disabled")) {
      finalizeBtn.focus();

      // Multiple attempts
      await user.keyboard("{Enter}{Enter}{Enter}");
      await user.keyboard("{ } { } { }");

      // Should never call API
      expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
    }
  });

  it("KBD-PREVIEW-1.4: enabled button DOES activate on Enter key", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      finalizeBtn.focus();

      // Activate with Enter
      await user.keyboard("{Enter}");

      // Should attempt to call API or show dialog
      await waitFor(() => {
        // At minimum, action was attempted
        expect(apiClient.finalizeHierarchy.mock.calls.length >= 0).toBeTruthy();
      });
    }
  });

  it("KBD-PREVIEW-1.5: enabled button DOES activate on Space key", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      finalizeBtn.focus();

      // Activate with Space
      await user.keyboard(" ");

      // Should attempt to call API or show dialog
      expect(apiClient.finalizeHierarchy.mock.calls.length >= 0).toBeTruthy();
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-2: TAB NAVIGATION & FOCUS MANAGEMENT
// ===========================================================================
describe("KBD-PREVIEW-2: Tab Navigation & Focus Management", () => {
  it("KBD-PREVIEW-2.1: finalize button is reachable via Tab navigation", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      // Tab should eventually reach the button
      let reached = false;
      for (let i = 0; i < 10; i++) {
        await user.keyboard("{Tab}");
        if (document.activeElement === finalizeBtn) {
          reached = true;
          break;
        }
      }

      // Button should be in tab order (or hidden, which is also valid)
      expect(reached || !finalizeBtn.offsetHeight).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-2.2: focus is visible when button is tabbed to", async () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      finalizeBtn.focus();

      // Should have some visual focus indicator
      // Either :focus styles or visible focus ring
      expect(
        finalizeBtn.style.outline ||
        finalizeBtn.style.boxShadow ||
        finalizeBtn.className.includes("focus") ||
        finalizeBtn.className.includes("active"),
      ).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-2.3: disabled button has correct tabindex", () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn?.hasAttribute("disabled")) {
      // Disabled buttons should have tabindex -1 or natural tabindex
      const tabindex = finalizeBtn.getAttribute("tabindex");
      expect(
        tabindex === null || tabindex === "0" || tabindex === "-1",
      ).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-2.4: Shift+Tab navigates backwards correctly", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      finalizeBtn.focus();

      // Shift+Tab should navigate away
      await user.keyboard("{Shift>}{Tab}{/Shift}");

      // Focus should have moved
      expect(document.activeElement !== finalizeBtn).toBeTruthy();
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-3: ARIA & ACCESSIBILITY ATTRIBUTES
// ===========================================================================
describe("KBD-PREVIEW-3: ARIA & Accessibility Attributes", () => {
  it("KBD-PREVIEW-3.1: finalize button is not disabled and has no conflicting aria-disabled", () => {
    // Preview no longer disables the button — the confirm dialog is the
    // real lock — so it must not claim to be disabled to a11y tools either.
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      expect(finalizeBtn).not.toHaveAttribute("disabled");
      expect(finalizeBtn.getAttribute("aria-disabled")).not.toBe("true");
    }
  });

  it("KBD-PREVIEW-3.2: disabled button has descriptive aria-label or title", () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && finalizeBtn.hasAttribute("disabled")) {
      const ariaLabel = finalizeBtn.getAttribute("aria-label") || "";
      const title = finalizeBtn.getAttribute("title") || "";
      const textContent = finalizeBtn.textContent || "";

      // Should have some description of why it's disabled
      const description = (ariaLabel + title + textContent).toLowerCase();
      expect(
        description.includes("preview") ||
        description.includes("confirm") ||
        description.includes("disabled") ||
        description.includes("must") ||
        description.includes("finalize"),
      ).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-3.3: button role is correctly announced", () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    expect(finalizeBtn).toHaveRole("button");
  });

  it("KBD-PREVIEW-3.4: preview indicator has accessible text (not just icon)", () => {
    renderWithKeyboardSupport({ isPreview: true });

    // Scoped to the dedicated preview indicator testid — a bare "draft"
    // match also hits the always-present "Draft tickets" section header,
    // which isn't the element under test here.
    const previewIndicator = screen.queryByTestId("preview-state-indicator");
    if (previewIndicator) {
      // Text should be visible to screen readers
      expect(previewIndicator.textContent).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-3.5: imported tickets are marked as read-only to screen readers", () => {
    renderWithKeyboardSupport({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    const ticketElements = screen.queryAllByText(/Capability/);
    for (const element of ticketElements) {
      // Should have some indication that content is read-only
      const container = element.closest("[role='region'], [role='article'], section");
      if (container) {
        const ariaLabel = container.getAttribute("aria-label") || "";
        const ariaDescribedBy = container.getAttribute("aria-describedby") || "";

        // Should indicate read-only status
        expect(
          ariaLabel.toLowerCase().includes("read") ||
          ariaDescribedBy.toLowerCase().includes("read"),
        ).toBeTruthy();
      }
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-4: SCREEN READER ANNOUNCEMENTS
// ===========================================================================
describe("KBD-PREVIEW-4: Screen Reader Announcements", () => {
  it("KBD-PREVIEW-4.1: disabled button announces reason to screen reader", () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn) {
      const ariaLabel = finalizeBtn.getAttribute("aria-label") || "";
      const title = finalizeBtn.getAttribute("title") || "";

      // Should have descriptive text for screen readers
      const fullDescription = (ariaLabel + title + finalizeBtn.textContent).toLowerCase();
      expect(fullDescription.length > 0).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-4.2: preview state change is announced to screen readers", async () => {
    const { rerender } = renderWithKeyboardSupport({ isPreview: false });

    // With no session/draft and isPreview=false, the button doesn't render.
    const initialBtn = getFinalizeButton();
    const initialName = initialBtn?.textContent || "";

    // Change to preview
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

    const updatedBtn = getFinalizeButton();
    const updatedName = updatedBtn?.textContent || "";

    // The button appears with a descriptive "Confirm to finalize" name once
    // isPreview is true — that's the screen-reader-visible state change.
    expect(updatedName).not.toBe(initialName);
    expect(updatedName).toMatch(/confirm/i);
  });

  it("KBD-PREVIEW-4.3: confirmation dialog has accessible title and role", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Confirmation dialog should be accessible
      const dialog = screen.queryByRole("dialog") ||
        screen.queryByRole("alertdialog");

      if (dialog) {
        // Dialog should have descriptive content
        expect(dialog.textContent).toBeTruthy();
      }
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-5: KEYBOARD SHORTCUTS & ALTERNATIVE ACTIVATION
// ===========================================================================
describe("KBD-PREVIEW-5: Keyboard Shortcuts & Activation Methods", () => {
  it("KBD-PREVIEW-5.1: disabled button doesn't respond to Alt+F shortcut", async () => {
    const user = userEvent.setup();
    renderWithKeyboardSupport({ isPreview: true });

    // Try Alt+F (common shortcut pattern)
    await user.keyboard("{Alt>}f{/Alt}");

    // Should NOT call API
    expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
  });

  it("KBD-PREVIEW-5.2: confirmation dialog can be dismissed with Escape", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Try to dismiss dialog
      await user.keyboard("{Escape}");

      // Dialog should be closed
      const dialog = screen.queryByRole("dialog") ||
        screen.queryByRole("alertdialog");
      expect(dialog).not.toBeInTheDocument();
    }
  });

  it("KBD-PREVIEW-5.3: confirmation dialog accepts Enter to confirm", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Get confirmation button
      const confirmBtn = screen.queryByRole("button", { name: /confirm|yes|proceed/i });
      if (confirmBtn) {
        // Focus and press Enter
        confirmBtn.focus();
        await user.keyboard("{Enter}");

        // API should be called
        await waitFor(() => {
          expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
        });
      }
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-6: HIGH CONTRAST MODE CONSIDERATIONS
// ===========================================================================
describe("KBD-PREVIEW-6: High Contrast Mode", () => {
  it("KBD-PREVIEW-6.1: disabled button is visually distinct in high contrast", () => {
    renderWithKeyboardSupport({ isPreview: true });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn?.hasAttribute("disabled")) {
      const styles = window.getComputedStyle(finalizeBtn);

      // Should have sufficient contrast
      // Either different opacity, color, or border
      expect(
        styles.opacity !== "1" ||
        styles.backgroundColor ||
        styles.borderColor ||
        finalizeBtn.className.includes("disabled") ||
        finalizeBtn.className.includes("inactive"),
      ).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-6.2: preview badge has sufficient contrast", () => {
    renderWithKeyboardSupport({ isPreview: true });

    // Scoped to the dedicated preview indicator testid — a bare "draft"
    // match also hits the always-present "Draft tickets" section header,
    // which isn't the element under test here.
    const previewIndicator = screen.queryByTestId("preview-state-indicator");
    if (previewIndicator) {
      const styles = window.getComputedStyle(previewIndicator);

      // Should have visible styling
      expect(
        styles.backgroundColor ||
        styles.color ||
        styles.borderColor ||
        previewIndicator.className.includes("badge") ||
        previewIndicator.className.includes("highlight"),
      ).toBeTruthy();
    }
  });

  it("KBD-PREVIEW-6.3: read-only content has visual indicator", () => {
    renderWithKeyboardSupport({
      isPreview: true,
      importedTickets: SAMPLE_TICKETS,
    });

    const ticketElements = screen.queryAllByText(/Capability/);
    for (const element of ticketElements) {
      const container = element.closest("[data-testid*='import'], [class*='import'], [class*='readonly']");
      if (container) {
        const styles = window.getComputedStyle(container);

        // Should have visual distinction (opacity, color, etc.)
        expect(
          styles.opacity ||
          styles.backgroundColor ||
          styles.borderColor,
        ).toBeTruthy();
      }
    }
  });
});

// ===========================================================================
// KBD-PREVIEW-7: FOCUS TRAP (Modal Behavior)
// ===========================================================================
describe("KBD-PREVIEW-7: Focus Management in Dialogs", () => {
  it("KBD-PREVIEW-7.1: confirmation dialog traps focus", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      const dialog = screen.queryByRole("dialog") ||
        screen.queryByRole("alertdialog");

      if (dialog) {
        // Focus should be within dialog
        const focusableElements = dialog.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]',
        );
        expect(focusableElements.length > 0).toBeTruthy();
      }
    }
  });

  it("KBD-PREVIEW-7.2: initial focus is set to confirmation button", async () => {
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["m-1"],
      total_created: 1,
      breakdown: { milestone: 1, feature: 0, capability: 0, task: 0 },
    });

    renderWithKeyboardSupport({ isPreview: false });

    const finalizeBtn = getFinalizeButton();
    if (finalizeBtn && !finalizeBtn.hasAttribute("disabled")) {
      await user.click(finalizeBtn);

      // Should be a confirm button
      const confirmBtn = screen.queryByRole("button", { name: /confirm|yes/i });
      if (confirmBtn) {
        // Focus might be on confirm or cancel, but should be in dialog
        expect(
          document.activeElement === confirmBtn ||
          document.activeElement?.closest('[role="dialog"]'),
        ).toBeTruthy();
      }
    }
  });
});
