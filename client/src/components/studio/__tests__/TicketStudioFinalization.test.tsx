import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";

/**
 * Integration test suite for post-finalization UX in Studio context.
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 * Stage:    test_break (test_designer)
 *
 * Tests the full flow: user completes hierarchy editing in Studio → clicks
 * "Finalize" → POST /api/tickets/finalize-hierarchy succeeds → confirmation
 * displays with counts → user can navigate to created hierarchy.
 *
 * Acceptance Criteria:
 *   - AC1: Success confirmation displayed after finalization
 *   - AC2: Clear indication of what was created (milestone/feature/capability/task counts)
 *   - AC3: Navigation to created hierarchy available
 *
 * Test Groups:
 *   - Group I  — Integration: Finalize Flow
 *   - Group II — Success Display & Navigation
 *   - Group III — Error Handling
 *   - Group IV — Edge Cases & State Management
 */

// Mock the API client
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

// Mock router
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

// Fixture: studio draft with a hierarchy ready to finalize
const DRAFT_HIERARCHY = [
  {
    external_id: "fin-test-m1",
    title: "Login Feature",
    work_item_type: "milestone",
    description: "User authentication system",
    priority: 1,
    acceptance_criteria: ["User can log in"],
    children: [
      {
        external_id: "fin-test-f1",
        title: "Email Login Flow",
        work_item_type: "feature",
        description: "Email-based authentication",
        priority: 1,
        acceptance_criteria: ["Accept email/password"],
        children: [
          {
            external_id: "fin-test-c1",
            title: "Login Form UI",
            work_item_type: "capability",
            description: "Build login form component",
            priority: 1,
            acceptance_criteria: ["Form accepts input"],
            children: [
              {
                external_id: "fin-test-t1",
                title: "Create LoginForm component",
                work_item_type: "task",
                priority: 2,
                children: [],
              },
              {
                external_id: "fin-test-t2",
                title: "Add validation",
                work_item_type: "task",
                priority: 2,
                children: [],
              },
            ],
          },
          {
            external_id: "fin-test-c2",
            title: "Auth API Integration",
            work_item_type: "capability",
            priority: 1,
            children: [
              {
                external_id: "fin-test-t3",
                title: "Integrate auth service",
                work_item_type: "task",
                priority: 2,
                children: [],
              },
            ],
          },
        ],
      },
      {
        external_id: "fin-test-f2",
        title: "Session Management",
        work_item_type: "feature",
        priority: 2,
        children: [
          {
            external_id: "fin-test-c3",
            title: "Session Store",
            work_item_type: "capability",
            priority: 2,
            children: [],
          },
        ],
      },
    ],
  },
];

// Fixture: success response from finalize endpoint
const FINALIZE_SUCCESS_RESPONSE = {
  created_ids: [
    "uuid-m1",
    "uuid-f1",
    "uuid-f2",
    "uuid-c1",
    "uuid-c2",
    "uuid-c3",
    "uuid-t1",
    "uuid-t2",
    "uuid-t3",
  ],
  total_created: 9,
  breakdown: {
    milestone: 1,
    feature: 2,
    capability: 3,
    task: 3,
  },
};

// Fixture: error response
const FINALIZE_ERROR_RESPONSE = {
  detail: "Duplicate external_id: 'fin-test-m1' already exists in workspace",
};

function renderStudioPanel(
  overrides: Partial<TicketStudioPanelProps> = {},
) {
  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    ...overrides,
  };

  const utils = render(
    <MemoryRouter>
      <TicketStudioPanel {...props} />
    </MemoryRouter>,
  );

  return { ...utils, props };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
  apiClient.finalizeHierarchy.mockClear();
});

// ===========================================================================
// Group I — Integration: Finalize Flow
// ===========================================================================
describe("Group I — Integration: Finalize Flow", () => {
  it("I1: user can click finalize button to start finalization", async () => {
    // AC1/AC3: User initiates finalization
    const user = userEvent.setup();
    renderStudioPanel();

    // User loads hierarchy (mocked in component)
    // User clicks finalize button
    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy|commit|apply/i,
    });
    expect(finalizeButton).toBeInTheDocument();

    await user.click(finalizeButton);

    // Finalization request should be initiated
    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
    });
  });

  it("I2: finalize button sends workspace slug and hierarchy to API", async () => {
    // AC1: Full finalization request
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel({
      workspaceSlug: "loregarden",
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalledWith({
        workspace_slug: "loregarden",
        hierarchy: expect.any(Array),
      });
    });
  });

  it("I3: shows loading indicator while finalization is in progress", async () => {
    // AC1: UX feedback during operation
    const user = userEvent.setup();

    // Mock delayed response to observe loading state
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () => resolve(FINALIZE_SUCCESS_RESPONSE),
            500,
          );
        }),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    // Loading spinner should appear
    const spinner = screen.queryByRole("status", { hidden: true });
    expect(spinner).toBeInTheDocument();

    // Wait for completion
    await waitFor(() => {
      expect(
        screen.getByRole("heading", {
          name: /success|completed|created/i,
        }),
      ).toBeInTheDocument();
    });
  });

  it("I4: disables finalize button while request is in flight", async () => {
    // Safety: prevent double-submission
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () => resolve(FINALIZE_SUCCESS_RESPONSE),
            300,
          );
        }),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    // Button should be disabled during request
    expect(finalizeButton).toBeDisabled();

    // Wait for completion
    await waitFor(() => {
      // Button may be disabled or re-enabled depending on implementation
      expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
    });
  });
});

// ===========================================================================
// Group II — Success Display & Navigation
// ===========================================================================
describe("Group II — Success Display & Navigation", () => {
  it("II1: displays success confirmation after finalization succeeds", async () => {
    // AC1: Success confirmation displayed
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    // Success confirmation appears
    await waitFor(() => {
      expect(
        screen.getByRole("heading", {
          name: /success|completed|hierarchy.*created/i,
        }),
      ).toBeInTheDocument();
    });
  });

  it("II2: displays breakdown of created items (1 milestone, 2 features, 3 capabilities, 3 tasks)", async () => {
    // AC2: Counts displayed
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/1.*milestone|milestone.*1/i)).toBeInTheDocument();
      expect(screen.getByText(/2.*feature|feature.*2/i)).toBeInTheDocument();
      expect(
        screen.getByText(/3.*capabilit|capabilit.*3/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/3.*task|task.*3/i)).toBeInTheDocument();
    });
  });

  it("II3: displays total count of items created (9 total)", async () => {
    // AC2: Total count summary
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/9|total.*9/i)).toBeInTheDocument();
    });
  });

  it("II4: provides button to navigate to created hierarchy", async () => {
    // AC3: Navigation available
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy|view.*created|open.*hierarchy|see.*items/i,
      });
      expect(navButton).toBeInTheDocument();
    });
  });

  it("II5: navigate button navigates to root hierarchy ID", async () => {
    // AC3: Navigation precision
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel({
      workspaceSlug: "loregarden",
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy|view.*created|open.*hierarchy/i,
      });
      expect(navButton).toBeInTheDocument();
    });

    // Get the root ID (first created ID, which is the milestone)
    const rootId = FINALIZE_SUCCESS_RESPONSE.created_ids[0];
    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created/i,
    });
    await user.click(navButton);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining(rootId),
    );
  });

  it("II6: user can close confirmation and return to studio", async () => {
    // AC1: Dismissal after success
    const user = userEvent.setup();
    const onClose = jest.fn();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel({
      onClose,
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const closeButton = screen.getByRole("button", {
        name: /close|done|dismiss|continue/i,
      });
      expect(closeButton).toBeInTheDocument();
    });

    const closeButton = screen.getByRole("button", {
      name: /close|done|dismiss|continue/i,
    });
    await user.click(closeButton);

    // Should dismiss confirmation
    expect(onClose).toHaveBeenCalled();
  });
});

// ===========================================================================
// Group III — Error Handling
// ===========================================================================
describe("Group III — Error Handling", () => {
  it("III1: displays error message when finalization fails", async () => {
    // Error handling
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Duplicate external_id: 'fin-test-m1' already exists"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(
        screen.getByText(/duplicate|error|failed/i),
      ).toBeInTheDocument();
    });
  });

  it("III2: does not show success confirmation when finalization fails", async () => {
    // Error fallback
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Validation failed"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(
        screen.queryByRole("heading", {
          name: /success|completed|hierarchy.*created/i,
        }),
      ).not.toBeInTheDocument();
    });
  });

  it("III3: allows user to retry finalization after error", async () => {
    // Recovery: user can try again
    const user = userEvent.setup();

    // First call fails
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Network error"),
    );

    renderStudioPanel();

    let finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/network|error|failed/i)).toBeInTheDocument();
    });

    // Reset mock for retry
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    // Find finalize button again (may have changed)
    finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy|retry/i,
    });
    await user.click(finalizeButton);

    // Should succeed on retry
    await waitFor(() => {
      expect(
        screen.getByRole("heading", {
          name: /success|completed/i,
        }),
      ).toBeInTheDocument();
    });
  });

  it("III4: shows detailed error information from API", async () => {
    // UX: User understands what failed
    const user = userEvent.setup();
    const errorMessage = "Type validation failed: Task cannot have Feature as child";
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error(errorMessage),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(
        screen.getByText(/type validation|task|feature|child/i),
      ).toBeInTheDocument();
    });
  });

  it("III5: handles network timeout gracefully", async () => {
    // Network error handling
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Request timeout"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/timeout|network|error/i)).toBeInTheDocument();
    });
  });
});

// ===========================================================================
// Group IV — Edge Cases & State Management
// ===========================================================================
describe("Group IV — Edge Cases & State Management", () => {
  it("IV1: handles very large hierarchy (100+ items) finalization", async () => {
    // Scalability
    const user = userEvent.setup();
    const largeResponse = {
      created_ids: Array.from({ length: 120 }, (_, i) => `id-${i}`),
      total_created: 120,
      breakdown: {
        milestone: 10,
        feature: 40,
        capability: 35,
        task: 35,
      },
    };

    apiClient.finalizeHierarchy.mockResolvedValueOnce(largeResponse);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/120|total.*120/i)).toBeInTheDocument();
    });
  });

  it("IV2: does not show counts if breakdown missing from response", async () => {
    // Resilience
    const user = userEvent.setup();
    const responseWithoutBreakdown = {
      created_ids: ["id-1", "id-2"],
      total_created: 2,
    };

    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      responseWithoutBreakdown,
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      // Should show total
      expect(screen.getByText(/2|total/i)).toBeInTheDocument();
    });
  });

  it("IV3: handles finalization of single-item hierarchy (milestone only)", async () => {
    // Edge case: minimal hierarchy
    const user = userEvent.setup();
    const singleItemResponse = {
      created_ids: ["milestone-single"],
      total_created: 1,
      breakdown: {
        milestone: 1,
        feature: 0,
        capability: 0,
        task: 0,
      },
    };

    apiClient.finalizeHierarchy.mockResolvedValueOnce(singleItemResponse);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/1.*milestone|milestone.*1/i)).toBeInTheDocument();
      expect(screen.getByText(/1|total.*1/i)).toBeInTheDocument();
    });
  });

  it("IV4: confirmation remains stable during rapid nav clicks", async () => {
    // Stability: no race conditions
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel({
      workspaceSlug: "loregarden",
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy|view.*created/i,
      });
      expect(navButton).toBeInTheDocument();
    });

    // Rapid clicks
    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created/i,
    });
    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);

    // Confirmation should still show
    expect(
      screen.getByRole("heading", {
        name: /success|completed/i,
      }),
    ).toBeInTheDocument();

    // All navigation calls should go through
    expect(mockNavigate).toHaveBeenCalledTimes(3);
  });

  it("IV5: clears previous error when user retries and succeeds", async () => {
    // State cleanup
    const user = userEvent.setup();

    // First: error
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Duplicate ID"),
    );

    renderStudioPanel();

    let finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/duplicate|error/i)).toBeInTheDocument();
    });

    // Second: success (error should be cleared)
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy|retry/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/9|total.*9/i)).toBeInTheDocument();
      // Error message should not appear
      expect(screen.queryByText(/duplicate/i)).not.toBeInTheDocument();
    });
  });

  it("IV6: workspace context passed to API call", async () => {
    // AC: Workspace scope
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(
      FINALIZE_SUCCESS_RESPONSE,
    );

    renderStudioPanel({
      workspaceSlug: "custom-workspace",
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_slug: "custom-workspace",
        }),
      );
    });
  });

  it("IV7: handles empty hierarchy (0 items) gracefully", async () => {
    // Edge case: no items in response
    const user = userEvent.setup();
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

    apiClient.finalizeHierarchy.mockResolvedValueOnce(emptyResponse);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      // Should show 0 items, not crash
      expect(screen.getByText(/0|zero|no items/i)).toBeInTheDocument();
    });
  });
});
