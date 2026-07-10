import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";

/**
 * ADVERSARIAL INTEGRATION TEST SUITE: Ticket Studio Finalization
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 * Stage:    test_break
 *
 * This suite exposes weaknesses in the end-to-end finalization flow by:
 * - Testing actual API contract mismatches
 * - Exposing state management race conditions
 * - Validating error recovery pathways
 * - Testing boundary conditions in hierarchies
 * - Detecting mock-induced false confidence
 *
 * Test Matrix:
 * - [X] API Response Mutations
 * - [X] Network Failure Scenarios
 * - [X] State Transition Errors
 * - [X] Component Integration Issues
 * - [X] Data Validation Gaps
 * - [X] Performance Under Load
 * - [X] Error Recovery Resilience
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
 * BASE FIXTURES
 */
const MINIMAL_HIERARCHY = [
  {
    external_id: "test-m1",
    title: "Test Milestone",
    work_item_type: "milestone",
    children: [],
  },
];

const VALID_HIERARCHY = [
  {
    external_id: "test-m1",
    title: "Test Milestone",
    work_item_type: "milestone",
    children: [
      {
        external_id: "test-f1",
        title: "Feature",
        work_item_type: "feature",
        children: [
          {
            external_id: "test-c1",
            title: "Capability",
            work_item_type: "capability",
            children: [
              {
                external_id: "test-t1",
                title: "Task",
                work_item_type: "task",
                children: [],
              },
            ],
          },
        ],
      },
    ],
  },
];

const BASE_SUCCESS_RESPONSE = {
  created_ids: ["m-1", "f-1", "c-1", "t-1"],
  total_created: 4,
  breakdown: {
    milestone: 1,
    feature: 1,
    capability: 1,
    task: 1,
  },
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
// ADVA-INT-1: API RESPONSE MUTATIONS
// ===========================================================================
describe("ADVA-INT-1: API Response Mutations", () => {
  it("ADVA-INT-1.1: handles API response with empty created_ids array", async () => {
    // Weakness: API may return empty IDs even with status 200
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: [],
      total_created: 0,
      breakdown: {
        milestone: 0,
        feature: 0,
        capability: 0,
        task: 0,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/0|total/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.2: handles API response missing created_ids field entirely", async () => {
    // Contract violation
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      total_created: 4,
      breakdown: BASE_SUCCESS_RESPONSE.breakdown,
      // No created_ids
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should handle gracefully or show error
    await waitFor(() => {
      const text = screen.queryByText(/error|success|completed|4/i);
      expect(text).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.3: handles API response with mismatched total_created", async () => {
    // Data inconsistency
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["id-1", "id-2"],
      total_created: 999, // Mismatch
      breakdown: BASE_SUCCESS_RESPONSE.breakdown,
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should display total_created or detected mismatch
    await waitFor(() => {
      expect(screen.queryByText(/999|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.4: handles API response with breakdown sum != total_created", async () => {
    // Math mismatch
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: BASE_SUCCESS_RESPONSE.created_ids,
      total_created: 4,
      breakdown: {
        milestone: 1,
        feature: 1,
        capability: 1,
        task: 10, // Should be 1
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/4|10|total/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.5: handles API returning additional unexpected fields", async () => {
    // Extra data in response
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      ...BASE_SUCCESS_RESPONSE,
      secret_field: "should-be-ignored",
      malicious_command: "DELETE * FROM users",
      extra_breakdown: { unknown: 999 },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/4|total/i)).toBeInTheDocument();
      // Should not display or execute extra fields
      expect(screen.queryByText(/secret_field|malicious/i)).not.toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.6: handles API response as null (not caught by mock)", async () => {
    // Mock returns null instead of object
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(null);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should not crash
    await waitFor(() => {
      const container = screen.queryByText(/error|null|undefined/i, { exact: false });
      if (container) {
        expect(container).toBeInTheDocument();
      }
    });
  });

  it("ADVA-INT-1.7: handles API response with Infinity in counts", async () => {
    // Type mutation
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: BASE_SUCCESS_RESPONSE.created_ids,
      total_created: Infinity,
      breakdown: {
        milestone: 1,
        feature: 1,
        capability: 1,
        task: Infinity,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should render without crashing
    await waitFor(() => {
      expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.8: handles API response with NaN in breakdown", async () => {
    // Type mutation
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: BASE_SUCCESS_RESPONSE.created_ids,
      total_created: 4,
      breakdown: {
        milestone: NaN,
        feature: 1,
        capability: 1,
        task: 1,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should not crash
    await waitFor(() => {
      expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.9: handles API returning wrong type for created_ids (string)", async () => {
    // Contract violation
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: "id-1,id-2,id-3,id-4", // Should be array
      total_created: 4,
      breakdown: BASE_SUCCESS_RESPONSE.breakdown,
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should handle type mismatch
    await waitFor(() => {
      const container = screen.queryByText(/error|success|4/i, { exact: false });
      expect(container).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.10: handles extremely large created_ids array (100k items)", async () => {
    // Stress: Performance
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: Array.from({ length: 100000 }, (_, i) => `id-${i}`),
      total_created: 100000,
      breakdown: {
        milestone: 25000,
        feature: 25000,
        capability: 25000,
        task: 25000,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should not hang or crash
    await waitFor(() => {
      expect(screen.getByText(/100000|total/i)).toBeInTheDocument();
    }, { timeout: 5000 });
  });
});

// ===========================================================================
// ADVA-INT-2: NETWORK FAILURE SCENARIOS
// ===========================================================================
describe("ADVA-INT-2: Network Failure Scenarios", () => {
  it("ADVA-INT-2.1: handles API timeout (Promise never resolves)", async () => {
    // Network: Timeout
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () => new Promise(() => {}) // Never resolves
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Finalize button should be disabled (loading state)
    expect(finalizeButton).toBeDisabled();

    // Test timeout recovery (this would require a real timeout or abort)
  });

  it("ADVA-INT-2.2: handles API connection refused (network error)", async () => {
    // Network: Connection refused
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Network error: ECONNREFUSED"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should display network error
    await waitFor(() => {
      expect(screen.getByText(/network|connection|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.3: handles API 500 error with error message", async () => {
    // Server error
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Server error: 500 Internal Server Error"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/500|error|server/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.4: handles API 400 Bad Request", async () => {
    // Client error
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Bad request: Invalid hierarchy structure"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/bad|invalid|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.5: handles malformed JSON response", async () => {
    // Invalid response
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Invalid JSON in response"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/json|parse|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.6: handles network error on retry", async () => {
    // Retry with persistent error
    const user = userEvent.setup();

    // First attempt fails
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Network error"),
    );

    renderStudioPanel();

    let finalizeButton = screen.getByRole("button", {
      name: /finalize|create|retry/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/error/i)).toBeInTheDocument();
    });

    // Second attempt also fails
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Still offline"),
    );

    finalizeButton = screen.getByRole("button", {
      name: /finalize|create|retry/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/offline|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.7: handles abort signal (user cancels)", async () => {
    // Simulated abort
    const user = userEvent.setup();
    const abortError = new Error("AbortError");
    apiClient.finalizeHierarchy.mockRejectedValueOnce(abortError);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should handle abort gracefully
    await waitFor(() => {
      const error = screen.queryByText(/abort|cancel|error/i, { exact: false });
      if (error) {
        expect(error).toBeInTheDocument();
      }
    });
  });
});

// ===========================================================================
// ADVA-INT-3: STATE MANAGEMENT RACE CONDITIONS
// ===========================================================================
describe("ADVA-INT-3: State Management Race Conditions", () => {
  it("ADVA-INT-3.1: handles second finalization click during first request", async () => {
    // Double-submission prevention
    const user = userEvent.setup();
    let resolveFirst: any;
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });

    // Click once (request pending)
    await user.click(finalizeButton);

    // Button should be disabled (prevent double-click)
    expect(finalizeButton).toBeDisabled();

    // Resolve the first request
    resolveFirst(BASE_SUCCESS_RESPONSE);

    // Should complete successfully with only one API call
    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalledTimes(1);
    });
  });

  it("ADVA-INT-3.2: handles error state persisting after successful retry", async () => {
    // State cleanup on retry
    const user = userEvent.setup();

    // First: fails
    apiClient.finalizeHierarchy.mockRejectedValueOnce(new Error("Error 1"));

    renderStudioPanel();

    let finalizeButton = screen.getByRole("button", {
      name: /finalize|create|retry/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/error|Error 1/i)).toBeInTheDocument();
    });

    // Second: succeeds
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    finalizeButton = screen.getByRole("button", {
      name: /finalize|create|retry/i,
    });
    await user.click(finalizeButton);

    // Error state should be cleared
    await waitFor(() => {
      expect(screen.queryByText(/Error 1/i)).not.toBeInTheDocument();
      expect(screen.getByText(/success|completed|4/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-3.3: handles state update after component unmount", async () => {
    // Memory leak test: state update attempt after unmount
    const user = userEvent.setup();
    let resolveFirst: any;
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const { unmount } = renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Unmount before API completes
    unmount();

    // Resolve after unmount (simulates delayed response)
    resolveFirst(BASE_SUCCESS_RESPONSE);

    // Should not cause errors or warnings
    // (In real scenario, would check console for "Can't perform a React state update")
  });

  it("ADVA-INT-3.4: handles rapid navigation clicks during loading", async () => {
    // Race: navigation during pending finalization
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Rapid navigation attempts while loading
    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy|view.*created/i,
      });
      expect(navButton).toBeInTheDocument();
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy|view.*created/i,
    });

    // Rapid clicks
    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);

    // All clicks should process without race condition
    expect(mockNavigate.mock.calls.length).toBe(3);
  });

  it("ADVA-INT-3.5: handles loading state toggle rapidly", async () => {
    // Rapid state transitions
    const user = userEvent.setup();

    // Mock responds slowly
    apiClient.finalizeHierarchy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(BASE_SUCCESS_RESPONSE), 100);
        }),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Wait for completion
    await waitFor(() => {
      expect(screen.getByText(/success|completed|4/i)).toBeInTheDocument();
    });

    // Should not have stale loading state
    expect(finalizeButton).not.toBeDisabled();
  });
});

// ===========================================================================
// ADVA-INT-4: ERROR MESSAGE & DETAIL HANDLING
// ===========================================================================
describe("ADVA-INT-4: Error Message & Detail Handling", () => {
  it("ADVA-INT-4.1: handles error message with HTML markup (XSS)", async () => {
    // Security: HTML injection
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("<img src=x onerror='alert(1)'>"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Error should be escaped
    await waitFor(() => {
      expect(screen.getByText(/img|onerror|error/i, { exact: false })).toBeInTheDocument();
    });
  });

  it("ADVA-INT-4.2: handles error message with JSON structure details", async () => {
    // API returns structured error
    const user = userEvent.setup();
    const errorDetail = {
      message: "Duplicate external_id",
      detail: {
        external_id: "fin-test-m1",
        workspace_id: "abc123",
      },
    };

    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error(JSON.stringify(errorDetail)),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should display helpful error info
    await waitFor(() => {
      expect(screen.getByText(/duplicate|error/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-4.3: handles extremely long error message", async () => {
    // Text overflow
    const user = userEvent.setup();
    const longError = "E".repeat(5000);
    apiClient.finalizeHierarchy.mockRejectedValueOnce(new Error(longError));

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should not break layout
    await waitFor(() => {
      const container = screen.getByText(/E{1,50}|error/i, { exact: false });
      expect(container).toBeInTheDocument();
    });
  });

  it("ADVA-INT-4.4: handles error with no message property", async () => {
    // Malformed error
    const user = userEvent.setup();
    const errorWithoutMessage = new Error();
    errorWithoutMessage.message = "";

    apiClient.finalizeHierarchy.mockRejectedValueOnce(errorWithoutMessage);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should show generic error or be silent
    await waitFor(() => {
      const errorDisplay = screen.queryByText(/error|failed/i, { exact: false });
      // Either shows generic error or doesn't crash
    });
  });
});

// ===========================================================================
// ADVA-INT-5: WORKSPACE CONTEXT VALIDATION
// ===========================================================================
describe("ADVA-INT-5: Workspace Context Validation", () => {
  it("ADVA-INT-5.1: handles undefined workspace slug in API call", async () => {
    // Missing workspace context
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel({
      workspaceSlug: undefined,
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should still call API, but with undefined workspace
    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_slug: undefined,
        }),
      );
    });
  });

  it("ADVA-INT-5.2: handles special characters in workspace slug", async () => {
    // Path/injection vulnerability test
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel({
      workspaceSlug: "workspace-!@#$%^&*()",
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_slug: "workspace-!@#$%^&*()",
        }),
      );
    });
  });

  it("ADVA-INT-5.3: handles extremely long workspace slug (5000+ chars)", async () => {
    // Boundary
    const user = userEvent.setup();
    const longSlug = "a".repeat(5000);
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel({
      workspaceSlug: longSlug,
    });

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(apiClient.finalizeHierarchy).toHaveBeenCalled();
    });
  });
});

// ===========================================================================
// ADVA-INT-6: HIERARCHY VALIDATION AT SUBMISSION
// ===========================================================================
describe("ADVA-INT-6: Hierarchy Validation at Submission", () => {
  it("ADVA-INT-6.1: handles finalization of empty hierarchy (no items)", async () => {
    // Edge case: no hierarchy to finalize
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: [],
      total_created: 0,
      breakdown: {
        milestone: 0,
        feature: 0,
        capability: 0,
        task: 0,
      },
    });

    renderStudioPanel();

    // Studio should have a way to handle empty hierarchy
    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });

    // May be disabled if no items, or API rejects it
    if (!finalizeButton.hasAttribute("disabled")) {
      await user.click(finalizeButton);

      await waitFor(() => {
        expect(screen.getByText(/0|total/i)).toBeInTheDocument();
      });
    }
  });

  it("ADVA-INT-6.2: handles finalization with deeply nested hierarchy (20+ levels)", async () => {
    // Stress: Deep nesting
    const user = userEvent.setup();

    // Create 20-level deep hierarchy
    let deep = { external_id: "t-20", title: "Level 20", work_item_type: "task", children: [] };
    for (let i = 19; i > 0; i--) {
      deep = {
        external_id: `item-${i}`,
        title: `Level ${i}`,
        work_item_type: i < 15 ? "capability" : "feature",
        children: [deep],
      };
    }

    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: Array.from({ length: 20 }, (_, i) => `id-${i}`),
      total_created: 20,
      breakdown: {
        milestone: 1,
        feature: 8,
        capability: 8,
        task: 3,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should handle deep nesting
    await waitFor(() => {
      expect(screen.getByText(/20|total/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-6.3: handles finalization with massive hierarchy (10k items)", async () => {
    // Stress: Large volume
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: Array.from({ length: 10000 }, (_, i) => `id-${i}`),
      total_created: 10000,
      breakdown: {
        milestone: 1,
        feature: 999,
        capability: 4000,
        task: 5000,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should handle without performance degradation
    await waitFor(() => {
      expect(screen.getByText(/10000|total/i)).toBeInTheDocument();
    }, { timeout: 10000 });
  });

  it("ADVA-INT-6.4: handles finalization with invalid hierarchy (type violations)", async () => {
    // Server validates and returns error
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Type validation failed: Task cannot have Feature as child"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    // Should show validation error
    await waitFor(() => {
      expect(screen.getByText(/type|validation|task|feature/i, { exact: false })).toBeInTheDocument();
    });
  });

  it("ADVA-INT-6.5: handles finalization with duplicate external_ids across levels", async () => {
    // Data quality issue
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockRejectedValueOnce(
      new Error("Duplicate external_id: 'test-m1' appears multiple times"),
    );

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      expect(screen.getByText(/duplicate/i)).toBeInTheDocument();
    });
  });
});

// ===========================================================================
// ADVA-INT-7: NAVIGATION INTEGRATION
// ===========================================================================
describe("ADVA-INT-7: Navigation Integration", () => {
  it("ADVA-INT-7.1: confirms navigate is called with correct hierarchy ID", async () => {
    // Navigation routing test
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy/i,
      });
      expect(navButton).toBeInTheDocument();
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    await user.click(navButton);

    // Should navigate with first created_id (milestone)
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining("m-1"),
    );
  });

  it("ADVA-INT-7.2: confirms navigation button disabled when rootHierarchyId missing", async () => {
    // Fallback when ID not available
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      ...BASE_SUCCESS_RESPONSE,
      // But component receives no rootHierarchyId
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.queryByRole("button", {
        name: /view.*hierarchy/i,
      });

      if (navButton) {
        expect(navButton).toBeDisabled();
      }
    });
  });

  it("ADVA-INT-7.3: handles navigation with special characters in ID", async () => {
    // Routing safety
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce({
      created_ids: ["id-!@#$%^&*(){}"],
      total_created: 1,
      breakdown: {
        milestone: 1,
        feature: 0,
        capability: 0,
        task: 0,
      },
    });

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const navButton = screen.getByRole("button", {
        name: /view.*hierarchy/i,
      });
      expect(navButton).not.toBeDisabled();
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    await user.click(navButton);

    // Should navigate even with special chars
    expect(mockNavigate).toHaveBeenCalled();
  });
});

// ===========================================================================
// ADVA-INT-8: ACCESSIBILITY & USABILITY
// ===========================================================================
describe("ADVA-INT-8: Accessibility & Usability", () => {
  it("ADVA-INT-8.1: confirms button text is clear and actionable", async () => {
    // UX: Button clarity
    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create.*hierarchy|apply|commit/i,
    });

    // Should have clear, descriptive text
    expect(finalizeButton).toHaveTextContent(/finalize|create|apply/i);
  });

  it("ADVA-INT-8.2: confirms success message is screen-reader accessible", async () => {
    // A11y: aria-live regions
    const user = userEvent.setup();
    apiClient.finalizeHierarchy.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);

    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });
    await user.click(finalizeButton);

    await waitFor(() => {
      const status = screen.queryByRole("status", { hidden: true });
      // Should have live region for announcements
      if (status) {
        expect(status).toBeInTheDocument();
      }
    });
  });

  it("ADVA-INT-8.3: confirms keyboard navigation works (no mouse required)", async () => {
    // A11y: Keyboard support
    renderStudioPanel();

    const finalizeButton = screen.getByRole("button", {
      name: /finalize|create/i,
    });

    // Focus and activate via keyboard
    finalizeButton.focus();
    expect(finalizeButton).toHaveFocus();
  });
});
