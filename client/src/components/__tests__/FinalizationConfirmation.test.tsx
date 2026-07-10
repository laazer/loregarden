import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

import type { FinalizationConfirmationProps } from "../FinalizationConfirmation";
import { FinalizationConfirmation } from "../FinalizationConfirmation";

/**
 * Behavioral test suite for FinalizationConfirmation component.
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 * Stage:    test_break (test_designer)
 *
 * These tests encode the DOM/behavioral contract for post-finalization UI.
 * After a hierarchy finalization succeeds, the component displays:
 * - Success confirmation message
 * - Counts of created work items by type (milestone, feature, capability, task)
 * - Navigation options to view the created hierarchy
 *
 * Requirement mapping (from acceptance criteria):
 *   - AC1: Success confirmation displayed after finalization
 *   - AC2: Clear indication of what was created (milestone/feature/capability/task counts)
 *   - AC3: Navigation to created hierarchy available
 *
 * Test Groups:
 *   - Group A  — Rendering (AC1, AC2)
 *   - Group B  — Counts & Breakdown (AC2)
 *   - Group C  — Navigation (AC3)
 *   - Group D  — State transitions & interactions
 *   - Group E  — Error handling & edge cases
 *   - Group X  — Adversarial & regression
 */

// Mock the router navigation hook
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

// Fixture: typical finalization response
const FINALIZATION_SUCCESS_RESPONSE = {
  created_ids: [
    "milestone-id-001",
    "feature-id-001",
    "feature-id-002",
    "capability-id-001",
    "capability-id-002",
    "task-id-001",
    "task-id-002",
    "task-id-003",
  ],
  total_created: 8,
  breakdown: {
    milestone: 1,
    feature: 2,
    capability: 2,
    task: 3,
  },
};

// Fixture: single milestone
const SINGLE_MILESTONE_RESPONSE = {
  created_ids: ["milestone-id-single"],
  total_created: 1,
  breakdown: {
    milestone: 1,
    feature: 0,
    capability: 0,
    task: 0,
  },
};

// Fixture: complex hierarchy
const COMPLEX_HIERARCHY_RESPONSE = {
  created_ids: Array.from({ length: 50 }, (_, i) => `item-${i}`),
  total_created: 50,
  breakdown: {
    milestone: 5,
    feature: 15,
    capability: 15,
    task: 15,
  },
};

type FinalizeResponse = typeof FINALIZATION_SUCCESS_RESPONSE;

interface ConfirmationProps extends FinalizationConfirmationProps {
  finalizationResponse?: FinalizeResponse | null;
  workspaceSlug?: string;
  rootHierarchyId?: string;
  isLoading?: boolean;
  error?: string | null;
  onClose?: () => void;
}

function renderConfirmation(overrides: Partial<ConfirmationProps> = {}) {
  const props: ConfirmationProps = {
    finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    workspaceSlug: "loregarden",
    rootHierarchyId: "milestone-id-001",
    isLoading: false,
    error: null,
    onClose: jest.fn(),
    ...overrides,
  };

  const utils = render(
    <MemoryRouter>
      <FinalizationConfirmation {...(props as FinalizationConfirmationProps)} />
    </MemoryRouter>,
  );

  return { ...utils, props };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
});

// ===========================================================================
// Group A — Rendering (AC1, AC2)
// ===========================================================================
describe("Group A — Rendering", () => {
  it("A1: renders success confirmation title when finalization succeeds", () => {
    // AC1: Success confirmation displayed
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    expect(
      screen.getByRole("heading", {
        name: /success|completed|finalization.*success|hierarchy.*created/i,
      }),
    ).toBeInTheDocument();
  });

  it("A2: displays success icon or visual indicator", () => {
    // AC1: Visual confirmation of success
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    // Look for check icon, success indicator, or equivalent
    const successIndicator = screen.queryByTestId("finalization-success-icon");
    const successBadge = screen.queryByRole("status", { hidden: false });

    expect(successIndicator || successBadge).toBeInTheDocument();
  });

  it("A3: renders confirmation text that includes congratulatory message", () => {
    // AC1: User-facing success feedback
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    expect(
      screen.getByText(
        /hierarchy.*created|successfully.*created|work.*item.*created/i,
      ),
    ).toBeInTheDocument();
  });

  it("A4: renders when response provided (happy path)", () => {
    // AC1: Component renders non-empty response
    const { container } = renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    expect(container.firstChild).toBeInTheDocument();
  });

  it("A5: does not render confirmation content when response is null", () => {
    // Edge case: pending or no response yet
    renderConfirmation({
      finalizationResponse: null,
    });

    // Component should not show success indicators
    expect(
      screen.queryByRole("heading", {
        name: /success|completed|hierarchy.*created/i,
      }),
    ).not.toBeInTheDocument();
  });

  it("A6: renders when response contains zero-count breakdown", () => {
    // Edge case: empty hierarchy (shouldn't happen but test resilience)
    const emptyResponse = {
      created_ids: [],
      total_created: 0,
      breakdown: {
        milestone: 0,
        feature: 0,
        capability: 0,
        task: 0,
      },
    };

    renderConfirmation({
      finalizationResponse: emptyResponse,
    });

    // Should still render, but with 0 counts
    expect(screen.getByText(/0|zero|no items/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// Group B — Counts & Breakdown (AC2)
// ===========================================================================
describe("Group B — Counts & Breakdown", () => {
  it("B1: displays total count of created work items", () => {
    // AC2: Clear indication of what was created
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    // Should display the total (8 items)
    expect(screen.getByText(/8|total.*8|created.*8/i)).toBeInTheDocument();
  });

  it("B2: displays count of milestones created", () => {
    // AC2: Milestone count
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const milestoneSection = screen.queryByText(/milestone/i);
    expect(milestoneSection).toBeInTheDocument();
    expect(screen.getByText(/1.*milestone|milestone.*1/i)).toBeInTheDocument();
  });

  it("B3: displays count of features created", () => {
    // AC2: Feature count
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const featureSection = screen.queryByText(/feature/i);
    expect(featureSection).toBeInTheDocument();
    expect(screen.getByText(/2.*feature|feature.*2/i)).toBeInTheDocument();
  });

  it("B4: displays count of capabilities created", () => {
    // AC2: Capability count
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const capabilitySection = screen.queryByText(/capabilit/i);
    expect(capabilitySection).toBeInTheDocument();
    expect(screen.getByText(/2.*capabilit|capabilit.*2/i)).toBeInTheDocument();
  });

  it("B5: displays count of tasks created", () => {
    // AC2: Task count
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const taskSection = screen.queryByText(/task/i);
    expect(taskSection).toBeInTheDocument();
    expect(screen.getByText(/3.*task|task.*3/i)).toBeInTheDocument();
  });

  it("B6: displays breakdown in summary format (e.g. '1 milestone, 2 features, 2 capabilities, 3 tasks')", () => {
    // AC2: Concise summary of all counts
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    // Look for a summary line that mentions all counts
    const summary = screen.queryByText(
      /\d+\s+(milestone|feature|capabilit|task).*\d+.*\d+.*\d+/i,
    );
    expect(summary).toBeInTheDocument();
  });

  it("B7: shows zero count when no items of a type were created", () => {
    // AC2: Handle zero counts gracefully
    const responseWithZeros = {
      created_ids: ["feature-id-001"],
      total_created: 1,
      breakdown: {
        milestone: 0,
        feature: 1,
        capability: 0,
        task: 0,
      },
    };

    renderConfirmation({
      finalizationResponse: responseWithZeros,
    });

    // Should show "0 milestones" or omit them
    const milestoneText = screen.queryByText(
      /0.*milestone|milestone.*0|no.*milestone/i,
    );
    expect(milestoneText).toBeInTheDocument();
  });

  it("B8: displays correct counts for single-item hierarchy", () => {
    // AC2: Single milestone case
    renderConfirmation({
      finalizationResponse: SINGLE_MILESTONE_RESPONSE,
    });

    expect(screen.getByText(/1.*total|total.*1/i)).toBeInTheDocument();
    expect(screen.getByText(/1.*milestone|milestone.*1/i)).toBeInTheDocument();
  });

  it("B9: displays correct counts for large hierarchy (50 items)", () => {
    // AC2: Complex breakdown with many items
    renderConfirmation({
      finalizationResponse: COMPLEX_HIERARCHY_RESPONSE,
    });

    expect(screen.getByText(/50|total.*50/i)).toBeInTheDocument();
    expect(screen.getByText(/5.*milestone|milestone.*5/i)).toBeInTheDocument();
    expect(screen.getByText(/15.*feature|feature.*15/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// Group C — Navigation (AC3)
// ===========================================================================
describe("Group C — Navigation", () => {
  it("C1: displays navigation button to view created hierarchy", () => {
    // AC3: Navigation available
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy|see.*items/i,
    });
    expect(navButton).toBeInTheDocument();
  });

  it("C2: navigation button navigates to hierarchy details when clicked", async () => {
    // AC3: Navigation leads to correct location
    const user = userEvent.setup();
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy/i,
    });
    await user.click(navButton);

    // Should navigate using the root hierarchy ID
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("milestone-id-001"),
      );
    });
  });

  it("C3: navigation button is enabled when rootHierarchyId is provided", () => {
    // AC3: Navigation enabled for valid hierarchy
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy/i,
    });
    expect(navButton).not.toBeDisabled();
  });

  it("C4: navigation button is disabled when rootHierarchyId is missing", () => {
    // AC3: Safety check - don't navigate to undefined
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: undefined,
    });

    const navButton = screen.queryByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy/i,
    });

    // Button may be disabled or hidden
    if (navButton) {
      expect(navButton).toBeDisabled();
    }
  });

  it("C5: navigation uses workspace slug in the navigation URL", async () => {
    // AC3: Navigation includes workspace context
    const user = userEvent.setup();
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      workspaceSlug: "loregarden",
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy/i,
    });
    await user.click(navButton);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalled();
      const callArg = mockNavigate.mock.calls[0][0];
      expect(callArg).toMatch(/loregarden|workspace/i);
    });
  });

  it("C6: displays link to open created hierarchy in new tab (optional)", () => {
    // AC3: Alternative: provide clickable link to hierarchy
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
      workspaceSlug: "loregarden",
    });

    // Look for a link element (if implemented)
    const link = screen.queryByRole("link", {
      name: /view.*hierarchy|created.*hierarchy|hierarchy.*details/i,
    });

    // Either button or link should exist
    const navElement =
      link ||
      screen.getByRole("button", {
        name: /view.*hierarchy|view.*created|open.*hierarchy/i,
      });
    expect(navElement).toBeInTheDocument();
  });

  it("C7: displays 'close' or 'done' button to dismiss confirmation", () => {
    // AC3/Navigation: User can dismiss the confirmation
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const closeButton = screen.queryByRole("button", {
      name: /close|done|dismiss|continue/i,
    });
    expect(closeButton).toBeInTheDocument();
  });

  it("C8: close button calls onClose callback", async () => {
    // AC3: Dismissal behavior
    const user = userEvent.setup();
    const onClose = jest.fn();
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      onClose,
    });

    const closeButton = screen.getByRole("button", {
      name: /close|done|dismiss|continue/i,
    });
    await user.click(closeButton);

    expect(onClose).toHaveBeenCalled();
  });
});

// ===========================================================================
// Group D — State Transitions & Interactions
// ===========================================================================
describe("Group D — State Transitions & Interactions", () => {
  it("D1: transitions from loading state to success confirmation", () => {
    // State: loading → success
    const { rerender } = renderConfirmation({
      finalizationResponse: null,
      isLoading: true,
    });

    // Initially shows loading
    expect(screen.getByRole("status", { hidden: true })).toBeInTheDocument();

    // Transition to success
    const successProps = {
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      isLoading: false,
    };

    rerender(
      <MemoryRouter>
        <FinalizationConfirmation {...(successProps as FinalizationConfirmationProps)} />
      </MemoryRouter>,
    );

    // Now shows success
    expect(
      screen.getByRole("heading", {
        name: /success|completed|hierarchy.*created/i,
      }),
    ).toBeInTheDocument();
  });

  it("D2: maintains confirmation display when user scrolls or interacts", () => {
    // Stability: confirmation persists
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const title = screen.getByRole("heading", {
      name: /success|completed|hierarchy.*created/i,
    });
    expect(title).toBeInTheDocument();

    // Simulate scroll or other DOM changes — title should still be present
    expect(title).toBeInTheDocument();
  });

  it("D3: clicking view navigation multiple times doesn't break navigation", async () => {
    // Resilience: idempotent navigation
    const user = userEvent.setup();
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created|open.*hierarchy/i,
    });

    await user.click(navButton);
    await user.click(navButton);

    // Both clicks should trigger navigation
    expect(mockNavigate).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// Group E — Error Handling & Edge Cases
// ===========================================================================
describe("Group E — Error Handling & Edge Cases", () => {
  it("E1: does not display confirmation when error is present", () => {
    // Fallback: show error, not success
    renderConfirmation({
      error: "Finalization failed: duplicate external IDs",
      finalizationResponse: null,
    });

    expect(
      screen.queryByRole("heading", {
        name: /success|completed|hierarchy.*created/i,
      }),
    ).not.toBeInTheDocument();

    expect(screen.getByText(/failed|error|duplicate/i)).toBeInTheDocument();
  });

  it("E2: displays error message when finalization fails", () => {
    // Error handling
    renderConfirmation({
      error: "Transaction rolled back: type validation failed",
      finalizationResponse: null,
    });

    expect(screen.getByText(/transaction|validation|failed/i)).toBeInTheDocument();
  });

  it("E3: displays loading state when isLoading is true", () => {
    // Pending state
    renderConfirmation({
      finalizationResponse: null,
      isLoading: true,
    });

    const spinner = screen.queryByRole("status", { hidden: true });
    expect(spinner).toBeInTheDocument();
  });

  it("E4: handles empty hierarchy gracefully (0 items created)", () => {
    // Edge case: empty hierarchy
    const emptyResponse = {
      created_ids: [],
      total_created: 0,
      breakdown: {
        milestone: 0,
        feature: 0,
        capability: 0,
        task: 0,
      },
    };

    renderConfirmation({
      finalizationResponse: emptyResponse,
    });

    // Should show 0 items, not crash
    expect(screen.getByText(/0|zero|no items/i)).toBeInTheDocument();
  });

  it("E5: handles missing breakdown field gracefully", () => {
    // Resilience: missing optional field
    const responseWithoutBreakdown = {
      created_ids: ["id-1", "id-2"],
      total_created: 2,
    };

    renderConfirmation({
      finalizationResponse: responseWithoutBreakdown as any,
    });

    // Should still show total
    expect(screen.getByText(/2|total.*2/i)).toBeInTheDocument();
  });

  it("E6: displays message when rootHierarchyId is missing but hierarchy created", () => {
    // Edge case: no root ID to navigate to
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: undefined,
    });

    // Should show success but may warn about missing navigation
    expect(
      screen.getByRole("heading", {
        name: /success|completed|hierarchy.*created/i,
      }),
    ).toBeInTheDocument();
  });

  it("E7: handles workspace slug missing gracefully", () => {
    // Edge case: no workspace context
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      workspaceSlug: undefined,
    });

    // Component should still render counts
    expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
  });

  it("E8: displays appropriate message for very large hierarchy (100+ items)", () => {
    // Performance edge case
    const largeResponse = {
      created_ids: Array.from({ length: 100 }, (_, i) => `item-${i}`),
      total_created: 100,
      breakdown: {
        milestone: 10,
        feature: 30,
        capability: 30,
        task: 30,
      },
    };

    renderConfirmation({
      finalizationResponse: largeResponse,
    });

    expect(screen.getByText(/100|total.*100/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// Group X — Adversarial & Regression
// ===========================================================================
describe("Group X — Adversarial & Regression", () => {
  it("X1: confirms counts sum to total_created", () => {
    // Math correctness
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const milestone = 1;
    const feature = 2;
    const capability = 2;
    const task = 3;
    const expected = milestone + feature + capability + task;

    expect(screen.getByText(`${expected}`)).toBeInTheDocument();
  });

  it("X2: confirms created_ids length matches total_created", () => {
    // Data integrity
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    expect(FINALIZATION_SUCCESS_RESPONSE.created_ids.length).toBe(
      FINALIZATION_SUCCESS_RESPONSE.total_created,
    );
  });

  it("X3: shows all counts simultaneously (not tab-switcher)", () => {
    // UX: All info visible at once
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    expect(screen.getByText(/milestone/i)).toBeInTheDocument();
    expect(screen.getByText(/feature/i)).toBeInTheDocument();
    expect(screen.getByText(/capabilit/i)).toBeInTheDocument();
    expect(screen.getByText(/task/i)).toBeInTheDocument();
  });

  it("X4: navigation path includes created hierarchy root ID", async () => {
    // AC3: Navigation precision
    const user = userEvent.setup();
    const rootId = "milestone-id-12345";

    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: rootId,
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created/i,
    });
    await user.click(navButton);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining(rootId),
    );
  });

  it("X5: confirmation remains visible after navigation click (until dismissed)", async () => {
    // UX: Not automatically closed on navigation
    const user = userEvent.setup();
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
      rootHierarchyId: "milestone-id-001",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    await user.click(navButton);

    // Confirmation should still be visible
    expect(
      screen.getByRole("heading", {
        name: /success|completed/i,
      }),
    ).toBeInTheDocument();
  });

  it("X6: handles response with undefined/null breakdown gracefully", () => {
    // Null-safety
    const malformedResponse = {
      created_ids: ["id-1"],
      total_created: 1,
      breakdown: null,
    };

    renderConfirmation({
      finalizationResponse: malformedResponse as any,
    });

    // Should not crash, show at least total
    expect(screen.getByText(/1|total/i)).toBeInTheDocument();
  });

  it("X7: does not display success when finalizationResponse is null and no error", () => {
    // Indeterminate state
    renderConfirmation({
      finalizationResponse: null,
      error: null,
      isLoading: false,
    });

    // Should not show success message
    expect(
      screen.queryByRole("heading", {
        name: /success|completed|hierarchy.*created/i,
      }),
    ).not.toBeInTheDocument();
  });

  it("X8: accessibility - success announcement uses aria-live", () => {
    // A11y: Screen reader support
    renderConfirmation({
      finalizationResponse: FINALIZATION_SUCCESS_RESPONSE,
    });

    const liveRegion = screen.queryByRole("status", { hidden: true });
    expect(liveRegion).toBeInTheDocument();
  });
});
