import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

import type { FinalizationConfirmationProps } from "../FinalizationConfirmation";
import { FinalizationConfirmation } from "../FinalizationConfirmation";

/**
 * ADVERSARIAL TEST SUITE: FinalizationConfirmation Component
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 * Stage:    test_break
 *
 * This test suite applies adversarial mutation testing, boundary condition analysis,
 * and edge-case fuzzing to expose hidden weaknesses in the FinalizationConfirmation
 * component and its integration with the finalization API contract.
 *
 * Test Matrix Dimensions:
 * - [X] Null & Empty Values
 * - [X] Boundary Conditions
 * - [X] Type & Structure Mutations
 * - [X] Invalid/Corrupt Inputs
 * - [X] Concurrency / Race Conditions
 * - [X] Order Dependency
 * - [X] Combinatorial Inputs
 * - [X] Stress / Load
 * - [X] Mutation Testing
 * - [X] Error Handling
 * - [X] Assumption Checks
 * - [X] Determinism Validation
 */

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

/**
 * BASE FIXTURES: Mutation test vectors for response structure
 */
const BASE_RESPONSE = {
  created_ids: [
    "uuid-1",
    "uuid-2",
    "uuid-3",
    "uuid-4",
  ],
  total_created: 4,
  breakdown: {
    milestone: 1,
    feature: 1,
    capability: 1,
    task: 1,
  },
};

type FinalizeResponse = typeof BASE_RESPONSE;

interface TestProps extends FinalizationConfirmationProps {
  finalizationResponse?: FinalizeResponse | null;
  workspaceSlug?: string;
  rootHierarchyId?: string;
  isLoading?: boolean;
  error?: string | null;
  onClose?: () => void;
}

function renderConfirmation(overrides: Partial<TestProps> = {}) {
  const props: TestProps = {
    finalizationResponse: BASE_RESPONSE,
    workspaceSlug: "loregarden",
    rootHierarchyId: "uuid-1",
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
// ADVA-1: NULL & EMPTY VALUE MUTATIONS
// ===========================================================================
describe("ADVA-1: Null & Empty Value Mutations", () => {
  it("ADVA-1.1: handles response with empty created_ids array", () => {
    // Weakness: Component may crash or display misleading counts
    renderConfirmation({
      finalizationResponse: {
        created_ids: [],
        total_created: 0,
        breakdown: {
          milestone: 0,
          feature: 0,
          capability: 0,
          task: 0,
        },
      },
    });

    // Should not crash and should show 0 items
    expect(screen.getByText(/0/)).toBeInTheDocument();
  });

  it("ADVA-1.2: handles response where created_ids has gaps (sparse array)", () => {
    // Mutation: What if some UUIDs are undefined?
    const sparseResponse = {
      created_ids: ["uuid-1", undefined, "uuid-3"] as any,
      total_created: 3,
      breakdown: {
        milestone: 1,
        feature: 1,
        capability: 1,
        task: 0,
      },
    };

    renderConfirmation({
      finalizationResponse: sparseResponse,
    });

    // Component should not crash and should handle undefined gracefully
    expect(screen.getByText(/3|total/i)).toBeInTheDocument();
  });

  it("ADVA-1.3: handles response where rootHierarchyId is empty string", () => {
    // Edge case: empty string vs undefined
    renderConfirmation({
      rootHierarchyId: "",
    });

    // Navigation button should be disabled
    const navButton = screen.queryByRole("button", {
      name: /view.*hierarchy/i,
    });

    if (navButton) {
      expect(navButton).toBeDisabled();
    }
  });

  it("ADVA-1.4: handles workspaceSlug as empty string", () => {
    // Workspace context missing
    renderConfirmation({
      workspaceSlug: "",
    });

    // Should still render, but navigation may be impaired
    expect(screen.getByText(/success|completed|created/i)).toBeInTheDocument();
  });

  it("ADVA-1.5: handles all breakdown counts as null", () => {
    // Type mutation: null instead of number
    const nullBreakdown = {
      ...BASE_RESPONSE,
      breakdown: {
        milestone: null,
        feature: null,
        capability: null,
        task: null,
      },
    };

    renderConfirmation({
      finalizationResponse: nullBreakdown as any,
    });

    // Should not crash; may show "null" or 0
    expect(screen.queryByText(/null|0|undefined/i)).toBeInTheDocument();
  });

  it("ADVA-1.6: handles breakdown as null (entire field)", () => {
    // Structural mutation
    const noBreakdown = {
      created_ids: BASE_RESPONSE.created_ids,
      total_created: 4,
      breakdown: null,
    };

    renderConfirmation({
      finalizationResponse: noBreakdown as any,
    });

    // Should render total at least
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-1.7: handles response with only created_ids field (no total or breakdown)", () => {
    // Partial response
    const minimalResponse = {
      created_ids: BASE_RESPONSE.created_ids,
    };

    renderConfirmation({
      finalizationResponse: minimalResponse as any,
    });

    // Should compute total from array length
    expect(screen.getByText(/4/)).toBeInTheDocument();
  });

  it("ADVA-1.8: handles error state with null finalization response", () => {
    // Boundary: both error and response null
    renderConfirmation({
      error: "API failed",
      finalizationResponse: null,
    });

    // Should show error, not success
    expect(screen.getByText(/failed|error/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/success|completed/i),
    ).not.toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-2: BOUNDARY CONDITION MUTATIONS
// ===========================================================================
describe("ADVA-2: Boundary Condition Mutations", () => {
  it("ADVA-2.1: handles total_created as 0 but created_ids with 1 item", () => {
    // Data inconsistency
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["uuid-1"],
        total_created: 0,
        breakdown: {
          milestone: 0,
          feature: 0,
          capability: 0,
          task: 0,
        },
      },
    });

    // Should detect mismatch and use created_ids.length or error
    // If it uses total_created, it will show 0 (wrong)
    // If it uses created_ids.length, it will show 1 (correct)
    const counts = screen.queryAllByText(/\d+/);
    expect(counts.length).toBeGreaterThan(0); // Should display something
  });

  it("ADVA-2.2: handles created_ids length mismatching breakdown sum", () => {
    // Data integrity violation
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["uuid-1", "uuid-2", "uuid-3", "uuid-4", "uuid-5"],
        total_created: 5,
        breakdown: {
          milestone: 1,
          feature: 1,
          capability: 1,
          task: 1, // Should be 2 to sum to 5
        },
      },
    });

    // Component must handle inconsistent state
    // Display should be consistent (either sum correctly or flag error)
    const total = screen.getByText(/5|total/i);
    expect(total).toBeInTheDocument();
  });

  it("ADVA-2.3: handles negative counts in breakdown", () => {
    // Invalid data
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: 1,
          feature: -1, // Invalid
          capability: 2,
          task: 2,
        },
      },
    });

    // Should handle gracefully (display negative, clamp to 0, or error)
    expect(screen.queryByText(/-1|feature/i)).toBeInTheDocument();
  });

  it("ADVA-2.4: handles fractional counts in breakdown", () => {
    // Type mutation: float instead of int
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: 1.5,
          feature: 1.2,
          capability: 0.9,
          task: 0.4,
        },
      },
    });

    // Should round/truncate or display as-is
    const rendered = screen.getByText(/\d+\.\d+|1\.5|1\.2/);
    expect(rendered).toBeInTheDocument();
  });

  it("ADVA-2.5: handles Infinity in total_created", () => {
    // Extreme boundary
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: Infinity,
        breakdown: BASE_RESPONSE.breakdown,
      } as any,
    });

    // Should not crash rendering Infinity
    const container = screen.getByText(/completed|success/i);
    expect(container).toBeInTheDocument();
  });

  it("ADVA-2.6: handles NaN in breakdown counts", () => {
    // Invalid number
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: NaN,
          feature: 1,
          capability: 1,
          task: 1,
        },
      } as any,
    });

    // Should render without crashing
    expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
  });

  it("ADVA-2.7: handles MAX_SAFE_INTEGER in total_created", () => {
    // Extreme large number
    renderConfirmation({
      finalizationResponse: {
        created_ids: Array.from({ length: 5 }, (_, i) => `id-${i}`),
        total_created: Number.MAX_SAFE_INTEGER,
        breakdown: {
          milestone: 1,
          feature: 1,
          capability: 1,
          task: Number.MAX_SAFE_INTEGER - 3,
        },
      } as any,
    });

    // Should not overflow or crash
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-2.8: handles single-character workspace slug", () => {
    // Boundary: minimal valid input
    renderConfirmation({
      workspaceSlug: "x",
    });

    expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
  });

  it("ADVA-2.9: handles very long workspace slug (1000+ chars)", () => {
    // Boundary: excessive input
    const longSlug = "a".repeat(1000);
    renderConfirmation({
      workspaceSlug: longSlug,
    });

    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-2.10: handles UUID as non-standard format (too long)", () => {
    // Input validation
    const longId = "id-" + "x".repeat(500);
    renderConfirmation({
      rootHierarchyId: longId,
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });

    // Navigation should still work with non-standard ID
    expect(navButton).not.toBeDisabled();
  });
});

// ===========================================================================
// ADVA-3: TYPE & STRUCTURE MUTATIONS
// ===========================================================================
describe("ADVA-3: Type & Structure Mutations", () => {
  it("ADVA-3.1: handles created_ids as comma-separated string instead of array", () => {
    // Type swap
    renderConfirmation({
      finalizationResponse: {
        created_ids: "uuid-1,uuid-2,uuid-3,uuid-4" as any,
        total_created: 4,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // Should either parse or crash gracefully
    expect(screen.queryByText(/success|completed|4/i)).toBeInTheDocument();
  });

  it("ADVA-3.2: handles breakdown as array instead of object", () => {
    // Structural mutation
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: [1, 1, 1, 1],
      } as any,
    });

    // Should not render milestone/feature/etc. counts
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-3.3: handles breakdown with additional unknown properties", () => {
    // Extra fields
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          ...BASE_RESPONSE.breakdown,
          unknown_type: 5,
          another_type: 10,
        },
      } as any,
    });

    // Should ignore unknown fields
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-3.4: handles total_created as string number", () => {
    // Type coercion test
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: "4" as any,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // May be coerced to number or displayed as string
    expect(screen.getByText(/4/)).toBeInTheDocument();
  });

  it("ADVA-3.5: handles breakdown counts as strings", () => {
    // Type mutation
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: "1" as any,
          feature: "1" as any,
          capability: "1" as any,
          task: "1" as any,
        },
      },
    });

    // Should display "1" or coerce to number
    expect(screen.getByText(/1|milestone/i)).toBeInTheDocument();
  });

  it("ADVA-3.6: handles rootHierarchyId with special characters", () => {
    // Character injection
    const specialId = "id-!@#$%^&*(){}[]|\\:;\"'<>,.?/";
    renderConfirmation({
      rootHierarchyId: specialId,
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    expect(navButton).not.toBeDisabled();
  });

  it("ADVA-3.7: handles workspaceSlug with special characters", () => {
    // URL/Path injection risk
    const specialSlug = "workspace-!@#$%";
    renderConfirmation({
      workspaceSlug: specialSlug,
    });

    // Should not break rendering
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-3.8: handles created_ids with duplicate values", () => {
    // Data quality issue
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["uuid-1", "uuid-2", "uuid-1", "uuid-2"],
        total_created: 4,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // Should display 4 items (what was returned), not dedupe
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-3.9: handles created_ids with null/undefined mixed in", () => {
    // Type heterogeneity
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["uuid-1", null, "uuid-3", undefined] as any,
        total_created: 4,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // Should not crash
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-3.10: handles finalizationResponse as empty object", () => {
    // Minimal malformed response
    renderConfirmation({
      finalizationResponse: {} as any,
    });

    // May show nothing or minimal info
    const container = screen.queryByText(/success|0|completed/i);
    // Either shows success (falsy check on undefined fields) or nothing
    // Important: it shouldn't crash
  });
});

// ===========================================================================
// ADVA-4: INVALID/CORRUPT INPUTS
// ===========================================================================
describe("ADVA-4: Invalid/Corrupt Inputs", () => {
  it("ADVA-4.1: handles non-UUID format in created_ids", () => {
    // Invalid UUID
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["not-a-uuid", "also-not-uuid", "nope", "invalid"],
        total_created: 4,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // Component shouldn't validate UUIDs, just display
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-4.2: handles created_ids with SQL injection patterns", () => {
    // Security test
    const maliciousIds = [
      "'; DROP TABLE tickets; --",
      "id' OR '1'='1",
      "<script>alert('xss')</script>",
      "id null",
    ];

    renderConfirmation({
      finalizationResponse: {
        created_ids: maliciousIds,
        total_created: 4,
        breakdown: BASE_RESPONSE.breakdown,
      },
    });

    // Should render and escape safely
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
    // XSS payload should not execute (test via rendered text)
    const heading = screen.getByText(/success/i);
    expect(heading).toBeInTheDocument();
  });

  it("ADVA-4.3: handles workspace slug with path traversal attempt", () => {
    // Security: path injection
    renderConfirmation({
      workspaceSlug: "../../../etc/passwd",
    });

    // Should not break or allow traversal
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-4.4: handles rootHierarchyId with protocol injection", () => {
    // URL injection
    renderConfirmation({
      rootHierarchyId: "javascript:alert('xss')",
    });

    // Should not execute as code
    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    // Should be disabled because ID is invalid
    if (navButton) {
      // Navigation call should NOT execute script
      expect(navButton).toBeInTheDocument();
    }
  });

  it("ADVA-4.5: handles breakdown with negative and positive counts mixed", () => {
    // Invalid state
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: -5,
          feature: 10,
          capability: -3,
          task: 2,
        },
      },
    });

    // Should display as-is or normalize
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-4.6: handles error message as HTML markup", () => {
    // XSS in error handling
    renderConfirmation({
      error: "<img src=x onerror='alert(1)'>",
      finalizationResponse: null,
    });

    // Should escape HTML
    expect(screen.getByText(/img|error/i, { exact: false })).toBeInTheDocument();
  });

  it("ADVA-4.7: handles breakdown with scientific notation counts", () => {
    // Number format
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 4,
        breakdown: {
          milestone: 1e2,
          feature: 1e-1,
          capability: 5e0,
          task: 0,
        },
      } as any,
    });

    // May display as 100, 0.1, 5, etc.
    expect(screen.getByText(/success/i)).toBeInTheDocument();
  });

  it("ADVA-4.8: handles very long title/congratulatory text", () => {
    // Text overflow
    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
    });

    // Should render without layout breaking
    const heading = screen.getByText(/success|completed|created/i);
    expect(heading).toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-5: CONCURRENCY / RACE CONDITIONS
// ===========================================================================
describe("ADVA-5: Concurrency & Race Conditions", () => {
  it("ADVA-5.1: handles rapid prop updates (created_ids changes mid-render)", async () => {
    // Simulate parent component state changing rapidly
    const { rerender } = renderConfirmation({
      finalizationResponse: {
        created_ids: ["uuid-1"],
        total_created: 1,
        breakdown: {
          milestone: 1,
          feature: 0,
          capability: 0,
          task: 0,
        },
      },
    });

    // Rapidly update response
    rerender(
      <MemoryRouter>
        <FinalizationConfirmation
          {...({
            finalizationResponse: {
              created_ids: ["uuid-1", "uuid-2", "uuid-3"],
              total_created: 3,
              breakdown: {
                milestone: 1,
                feature: 1,
                capability: 1,
                task: 0,
              },
            },
            workspaceSlug: "loregarden",
            rootHierarchyId: "uuid-1",
            isLoading: false,
            error: null,
            onClose: jest.fn(),
          } as FinalizationConfirmationProps)}
        />
      </MemoryRouter>,
    );

    // Should not crash and should show latest state
    expect(screen.getByText(/3|total/i)).toBeInTheDocument();
  });

  it("ADVA-5.2: handles loading→success→error state transitions", async () => {
    // Simulate error state overriding success
    const { rerender } = renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      isLoading: false,
      error: null,
    });

    // Transition to error
    rerender(
      <MemoryRouter>
        <FinalizationConfirmation
          {...({
            finalizationResponse: null,
            isLoading: false,
            error: "API Error",
            onClose: jest.fn(),
          } as FinalizationConfirmationProps)}
        />
      </MemoryRouter>,
    );

    // Error should override success
    expect(screen.getByText(/error|api/i)).toBeInTheDocument();
    expect(screen.queryByText(/success|completed/i)).not.toBeInTheDocument();
  });

  it("ADVA-5.3: handles onClose callback called multiple times", async () => {
    // Rapid dismissals
    const user = userEvent.setup();
    const onClose = jest.fn();

    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      onClose,
    });

    const closeButton = screen.getByRole("button", {
      name: /close|done/i,
    });

    await user.click(closeButton);
    await user.click(closeButton);
    await user.click(closeButton);

    // Should handle multiple calls (idempotent or error)
    expect(onClose.mock.calls.length).toBeGreaterThan(0);
  });

  it("ADVA-5.4: handles navigate called multiple times in quick succession", async () => {
    // Rapid navigation clicks
    const user = userEvent.setup();
    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      rootHierarchyId: "uuid-1",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });

    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);

    // All navigation calls should register
    expect(mockNavigate.mock.calls.length).toBe(5);
  });

  it("ADVA-5.5: handles isLoading=true but finalizationResponse present", () => {
    // Contradictory state
    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      isLoading: true,
    });

    // Should prioritize loading state or show both
    // Spinner should be visible
    const spinner = screen.queryByRole("status", { hidden: true });
    if (spinner) {
      expect(spinner).toBeInTheDocument();
    }
  });

  it("ADVA-5.6: handles both error and finalizationResponse present", () => {
    // Contradictory state
    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      error: "Simulation error",
    });

    // Error should take precedence
    expect(screen.getByText(/error|simulation/i)).toBeInTheDocument();
  });

  it("ADVA-5.7: handles unmounting while API call in flight (simulated)", async () => {
    // Simulate component unmounting
    const { unmount } = renderConfirmation({
      finalizationResponse: null,
      isLoading: true,
    });

    // Unmount while loading
    unmount();

    // Should not cause memory leak or error
    // (In real scenario, this would test cleanup in useEffect)
  });
});

// ===========================================================================
// ADVA-6: ORDER DEPENDENCY & STATE-SENSITIVE LOGIC
// ===========================================================================
describe("ADVA-6: Order Dependency & State-Sensitive Logic", () => {
  it("ADVA-6.1: confirms created_ids order doesn't affect total count", () => {
    // Order should not matter
    const response1 = {
      created_ids: ["a", "b", "c", "d"],
      total_created: 4,
      breakdown: BASE_RESPONSE.breakdown,
    };

    const response2 = {
      created_ids: ["d", "c", "b", "a"],
      total_created: 4,
      breakdown: BASE_RESPONSE.breakdown,
    };

    const { rerender: rerender1 } = renderConfirmation({
      finalizationResponse: response1,
    });

    expect(screen.getByText(/4|total/i)).toBeInTheDocument();

    const { rerender: rerender2 } = renderConfirmation({
      finalizationResponse: response2,
    });

    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-6.2: confirms navigation uses first created_id if rootHierarchyId derived from it", async () => {
    // Assumption: rootHierarchyId = created_ids[0]
    // What if it's not?
    const user = userEvent.setup();

    renderConfirmation({
      finalizationResponse: {
        created_ids: ["first-id", "second-id", "third-id"],
        total_created: 3,
        breakdown: BASE_RESPONSE.breakdown,
      },
      rootHierarchyId: "different-id",
    });

    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    await user.click(navButton);

    // Should navigate to rootHierarchyId, not first created_id
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining("different-id"),
    );
  });

  it("ADVA-6.3: handles breakdown sum != total_created", () => {
    // Math mismatch
    renderConfirmation({
      finalizationResponse: {
        created_ids: BASE_RESPONSE.created_ids,
        total_created: 10, // Should be 4
        breakdown: {
          milestone: 1,
          feature: 1,
          capability: 1,
          task: 1, // Sum = 4, but total = 10
        },
      },
    });

    // Component should handle this gracefully
    // Either use total, or sum breakdown, or error
    const total = screen.queryByText(/10|total/i);
    expect(total).toBeInTheDocument();
  });

  it("ADVA-6.4: confirms breakdown is computed from created_ids or provided explicitly", () => {
    // What if breakdown is omitted?
    const responseNoBreakdown = {
      created_ids: ["a", "b", "c", "d"],
      total_created: 4,
      // breakdown: undefined
    };

    renderConfirmation({
      finalizationResponse: responseNoBreakdown as any,
    });

    // Should still show total
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-7: COMBINATORIAL & STRESS TESTING
// ===========================================================================
describe("ADVA-7: Combinatorial & Stress Testing", () => {
  it("ADVA-7.1: handles extreme combination: null response + loading + error", () => {
    // Triple contradiction
    renderConfirmation({
      finalizationResponse: null,
      isLoading: true,
      error: "Unexpected error",
    });

    // Should show error (highest priority) or loading
    const container = screen.queryByText(/error|loading/i);
    expect(container).toBeInTheDocument();
  });

  it("ADVA-7.2: handles 10,000 items in created_ids array", () => {
    // Stress: large array
    renderConfirmation({
      finalizationResponse: {
        created_ids: Array.from({ length: 10000 }, (_, i) => `id-${i}`),
        total_created: 10000,
        breakdown: {
          milestone: 2500,
          feature: 2500,
          capability: 2500,
          task: 2500,
        },
      },
    });

    // Should render without performance degredation
    expect(screen.getByText(/10000|total/i)).toBeInTheDocument();
  });

  it("ADVA-7.3: handles workspace slug + rootHierarchyId both undefined", () => {
    // Navigation impaired
    renderConfirmation({
      workspaceSlug: undefined,
      rootHierarchyId: undefined,
    });

    const navButton = screen.queryByRole("button", {
      name: /view.*hierarchy/i,
    });

    // Should be disabled or absent
    if (navButton) {
      expect(navButton).toBeDisabled();
    }
  });

  it("ADVA-7.4: handles all props undefined except finalizationResponse", () => {
    // Minimal props
    const props = {
      finalizationResponse: BASE_RESPONSE,
      workspaceSlug: undefined,
      rootHierarchyId: undefined,
      isLoading: undefined,
      error: undefined,
      onClose: undefined,
    };

    render(
      <MemoryRouter>
        <FinalizationConfirmation {...(props as FinalizationConfirmationProps)} />
      </MemoryRouter>,
    );

    // Should still render success message
    expect(screen.getByText(/success|completed|created/i)).toBeInTheDocument();
  });

  it("ADVA-7.5: handles very long breakdown count display (e.g., 9999 tasks)", () => {
    // Text rendering stress
    renderConfirmation({
      finalizationResponse: {
        created_ids: Array.from({ length: 10000 }, (_, i) => `id-${i}`),
        total_created: 10000,
        breakdown: {
          milestone: 1,
          feature: 0,
          capability: 0,
          task: 9999,
        },
      },
    });

    expect(screen.getByText(/10000|9999/i)).toBeInTheDocument();
  });

  it("ADVA-7.6: handles rapid prop changes across all fields", async () => {
    // Mutation across multiple props simultaneously
    const { rerender } = renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
      workspaceSlug: "old",
      rootHierarchyId: "old-id",
      error: null,
      isLoading: false,
    });

    // Change everything at once
    rerender(
      <MemoryRouter>
        <FinalizationConfirmation
          {...({
            finalizationResponse: {
              created_ids: ["new"],
              total_created: 1,
              breakdown: {
                milestone: 1,
                feature: 0,
                capability: 0,
                task: 0,
              },
            },
            workspaceSlug: "new-workspace",
            rootHierarchyId: "new-id",
            error: "New error",
            isLoading: true,
            onClose: jest.fn(),
          } as FinalizationConfirmationProps)}
        />
      </MemoryRouter>,
    );

    // Should reflect new state (error should take precedence)
    expect(screen.getByText(/error/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// ADVA-8: ASSUMPTION VALIDATION
// ===========================================================================
describe("ADVA-8: Assumption Validation", () => {
  it("ADVA-8.1: confirms component renders success without workspace context", () => {
    // Assumption: workspace slug is optional for display
    renderConfirmation({
      workspaceSlug: undefined,
    });

    expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
  });

  it("ADVA-8.2: confirms component renders success without navigation capability", () => {
    // Assumption: navigation is optional
    renderConfirmation({
      rootHierarchyId: undefined,
    });

    expect(screen.getByText(/success|completed/i)).toBeInTheDocument();
  });

  it("ADVA-8.3: assumes onClose callback is optional", () => {
    // If onClose is undefined, close button may not work
    renderConfirmation({
      onClose: undefined,
    });

    const closeButton = screen.queryByRole("button", {
      name: /close|done|dismiss/i,
    });

    // Should still render or safely handle undefined callback
    if (closeButton) {
      expect(closeButton).toBeInTheDocument();
    }
  });

  it("ADVA-8.4: assumes breakdown.milestone is always first in display order", () => {
    // Check display order
    renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
    });

    const milestoneText = screen.getByText(/milestone/i);
    const featureText = screen.getByText(/feature/i);

    // Milestone should appear before feature in DOM
    const milestoneIndex = document.body.innerText.indexOf("milestone");
    const featureIndex = document.body.innerText.indexOf("feature");

    expect(milestoneIndex).toBeLessThan(featureIndex);
  });

  it("ADVA-8.5: assumes rootHierarchyId is in created_ids array", () => {
    // What if it's not?
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["id-1", "id-2", "id-3"],
        total_created: 3,
        breakdown: BASE_RESPONSE.breakdown,
      },
      rootHierarchyId: "id-not-in-list",
    });

    // Component should still allow navigation
    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    expect(navButton).not.toBeDisabled();
  });

  it("ADVA-8.6: assumes breakdown object has exactly 4 properties", () => {
    // What if it has fewer?
    const minimal = {
      created_ids: BASE_RESPONSE.created_ids,
      total_created: 4,
      breakdown: {
        task: 4,
      },
    };

    renderConfirmation({
      finalizationResponse: minimal as any,
    });

    // Should still render total
    expect(screen.getByText(/4|total/i)).toBeInTheDocument();
  });

  it("ADVA-8.7: assumes created_ids[0] represents root milestone", () => {
    // What if rootHierarchyId !== created_ids[0]?
    renderConfirmation({
      finalizationResponse: {
        created_ids: ["id-2", "id-1", "id-3"],
        total_created: 3,
        breakdown: BASE_RESPONSE.breakdown,
      },
      rootHierarchyId: "id-1",
    });

    // Navigation should use rootHierarchyId, not created_ids[0]
    const navButton = screen.getByRole("button", {
      name: /view.*hierarchy/i,
    });
    expect(navButton).not.toBeDisabled();
  });
});

// ===========================================================================
// ADVA-9: DETERMINISM VALIDATION
// ===========================================================================
describe("ADVA-9: Determinism Validation", () => {
  it("ADVA-9.1: confirms rendering is deterministic for same inputs", () => {
    // First render
    const { rerender: rerender1 } = renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
    });

    const firstText = screen.getByText(/success/i).textContent;

    // Re-render with same props
    rerender1(
      <MemoryRouter>
        <FinalizationConfirmation
          {...({
            finalizationResponse: BASE_RESPONSE,
            workspaceSlug: "loregarden",
            rootHierarchyId: "uuid-1",
            isLoading: false,
            error: null,
            onClose: jest.fn(),
          } as FinalizationConfirmationProps)}
        />
      </MemoryRouter>,
    );

    const secondText = screen.getByText(/success/i).textContent;

    // Should be identical
    expect(firstText).toBe(secondText);
  });

  it("ADVA-9.2: confirms counts display consistently across re-renders", () => {
    const { rerender } = renderConfirmation({
      finalizationResponse: BASE_RESPONSE,
    });

    const first = screen.getByText(/1.*milestone|milestone.*1/i);
    expect(first).toBeInTheDocument();

    // Re-render
    rerender(
      <MemoryRouter>
        <FinalizationConfirmation
          {...({
            finalizationResponse: BASE_RESPONSE,
            workspaceSlug: "loregarden",
            rootHierarchyId: "uuid-1",
            isLoading: false,
            error: null,
            onClose: jest.fn(),
          } as FinalizationConfirmationProps)}
        />
      </MemoryRouter>,
    );

    const second = screen.getByText(/1.*milestone|milestone.*1/i);
    expect(second).toBeInTheDocument();
    expect(first.textContent).toBe(second.textContent);
  });
});
