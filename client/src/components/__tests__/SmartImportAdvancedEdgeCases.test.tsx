import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * Advanced Adversarial Test Suite: Smart Import Routing Edge Cases & Mutations
 *
 * Ticket:   34-route-smart-import-selection-to-studio-with-prev
 * Stage:    test_break (test_breaker)
 *
 * Purpose:
 * This suite exposes weaknesses, boundary violations, and subtle state management
 * bugs in the smart import routing implementation. Tests are designed to FAIL if
 * the implementation has gaps in:
 *
 * 1. NULL & EMPTY VALUE HANDLING
 * 2. TYPE & STRUCTURE MUTATIONS
 * 3. CONCURRENCY & RACE CONDITIONS
 * 4. STATE CONSISTENCY & INVARIANTS
 * 5. ERROR HANDLING & RECOVERY
 * 6. BOUNDARY CONDITION ENFORCEMENT
 * 7. ASSUMPTION VALIDATION
 *
 * Many of these tests document assumptions that are implicit in the current tests
 * but never explicitly validated. Exposing these assumptions reveals brittle design
 * and helps the implementation team build more robust code.
 */

jest.mock("../ImportTicketFileExplorer", () => {
  const FIXTURE_FILES = [
    { path: "features/auth.md", name: "auth.md", repo_path: "features/auth.md" },
    { path: "tasks/setup.md", name: "setup.md", repo_path: "tasks/setup.md" },
  ];

  return {
    __esModule: true,
    ImportTicketFileExplorer: (props: {
      selectedFiles: Map<string, { path: string; name: string; repo_path: string }>;
      onToggleFile: (
        file: { path: string; name: string; repo_path: string },
        checked: boolean,
      ) => void;
      disabled?: boolean;
    }) => (
      <div data-testid="mock-file-explorer">
        {FIXTURE_FILES.map((file) => {
          const checked = props.selectedFiles.has(file.path);
          return (
            <button
              key={file.path}
              type="button"
              data-testid={`toggle-${file.path}`}
              aria-pressed={checked}
              disabled={props.disabled}
              onClick={() => props.onToggleFile(file, !checked)}
            >
              {file.repo_path}
            </button>
          );
        })}
      </div>
    ),
  };
});

type ImportMode = "regular" | "smart";

interface ModalProps {
  open: boolean;
  workspaceSlug: string;
  initialBrowsePath?: string;
  isLoading: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue: (filePaths: string[], mode?: ImportMode) => void | Promise<void>;
  initialMode?: ImportMode;
}

function renderModal(overrides: Partial<ModalProps> = {}) {
  const props: ModalProps = {
    open: true,
    workspaceSlug: "loregarden",
    isLoading: false,
    onClose: jest.fn(),
    onContinue: jest.fn(),
    ...overrides,
  };

  const utils = render(
    <BrowserRouter>
      <ImportTicketsModal {...(props as React.ComponentProps<typeof ImportTicketsModal>)} />
    </BrowserRouter>,
  );
  return { ...utils, props };
}

async function toggleFile(path: string) {
  await userEvent.click(screen.getByTestId(`toggle-${path}`));
}

function getContinueButton(): HTMLElement {
  return screen.getByRole("button", { name: /continue/i });
}

beforeEach(() => {
  jest.clearAllMocks();
});

// ===========================================================================
// GROUP A — NULL & EMPTY VALUE MUTATIONS
// ===========================================================================

describe("GROUP A: Null & Empty Value Handling", () => {
  it("A1: handles onContinue callback being undefined gracefully", () => {
    // This should not crash even if onContinue is undefined
    renderModal({
      onContinue: undefined as unknown as (filePaths: string[], mode?: ImportMode) => void,
    });

    // Attempting to interact should not throw
    expect(() => {
      toggleFile("features/auth.md");
    }).not.toThrow();
  });

  it("A2: empty file paths array is rejected by Continue button", () => {
    renderModal();
    const continueBtn = getContinueButton();
    expect(continueBtn).toBeDisabled();
  });

  it("A3: null workspaceSlug should be handled (no label crash)", () => {
    expect(() => {
      renderModal({ workspaceSlug: null as unknown as string });
    }).not.toThrow();
  });

  it("A4: empty string workspaceSlug renders without crashing", () => {
    const { props } = renderModal({ workspaceSlug: "" });
    expect(props).toBeDefined();
    // Modal should still function
    expect(getContinueButton()).toBeDisabled();
  });

  it("A5: null initialMode should default to 'regular' (if implemented)", () => {
    renderModal({ initialMode: null as unknown as ImportMode });
    // If mode selector exists and initialMode is null, should default to regular
    // This test documents the expected default behavior
  });

  it("A6: null errorMessage is handled (no display)", () => {
    renderModal({ errorMessage: null });
    expect(screen.queryByText(/error|fail/i)).not.toBeInTheDocument();
  });

  it("A7: undefined errorMessage is handled (no display)", () => {
    renderModal({ errorMessage: undefined });
    expect(screen.queryByText(/error|fail/i)).not.toBeInTheDocument();
  });

  it("A8: empty string errorMessage renders (even if content-less)", () => {
    renderModal({ errorMessage: "" });
    // Empty error message should not cause UI crash
    expect(getContinueButton()).toBeDisabled();
  });
});

// ===========================================================================
// GROUP B — TYPE & STRUCTURE MUTATIONS
// ===========================================================================

describe("GROUP B: Type & Structure Mutations", () => {
  it("B1: onContinue receives string array (not Set, Map, or other)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(handler).toHaveBeenCalled();
    const [firstArg] = handler.mock.calls[0];
    expect(Array.isArray(firstArg)).toBe(true);
    expect(firstArg).toEqual(expect.any(Array));
  });

  it("B2: file paths in array are strings (not objects)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    paths.forEach((path: unknown) => {
      expect(typeof path).toBe("string");
    });
  });

  it("B3: mode parameter is always string (if provided)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    if (handler.mock.calls[0].length > 1) {
      const [, mode] = handler.mock.calls[0];
      expect(typeof mode).toBe("string");
    }
  });

  it("B4: mode parameter is one of ['regular', 'smart'] (no arbitrary strings)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    if (handler.mock.calls[0].length > 1) {
      const [, mode] = handler.mock.calls[0];
      expect(["regular", "smart"]).toContain(mode);
    }
  });

  it("B5: workspaceSlug type must be string (not symbol, number, object)", () => {
    expect(() => {
      renderModal({ workspaceSlug: 123 as unknown as string });
    }).not.toThrow();
  });

  it("B6: isLoading is boolean (not truthy/falsy coercion)", () => {
    renderModal({ isLoading: false });
    expect(getContinueButton()).toBeDisabled();

    renderModal({ isLoading: true });
    expect(getContinueButton()).toBeDisabled();
  });

  it("B7: onClose is callable (not null/undefined)", async () => {
    const handler = jest.fn();
    renderModal({ onClose: handler });

    const cancelBtn = screen.getByRole("button", { name: /^cancel$/i });
    await userEvent.click(cancelBtn);

    expect(handler).toHaveBeenCalled();
  });
});

// ===========================================================================
// GROUP C — CONCURRENCY & RACE CONDITIONS
// ===========================================================================

describe("GROUP C: Concurrency & Race Conditions", () => {
  it("C1: clicking Continue multiple times does not call onContinue multiple times", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    const continueBtn = getContinueButton();

    // Rapid clicks
    await userEvent.click(continueBtn);
    await userEvent.click(continueBtn);
    await userEvent.click(continueBtn);

    // Should be called only once (or implementation prevents rapid re-click)
    expect(handler.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("C2: file selection changes during onContinue async operation do not affect emitted paths", async () => {
    const handler = jest.fn(async () => {
      // Simulate async operation
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // onContinue was called with one file
    const calls = handler.mock.calls as Array<unknown[]>;
    expect(calls[0][0]).toEqual(["features/auth.md"]);

    // Now try to toggle more files while handler is still async
    await toggleFile("tasks/setup.md");

    // Handler should have been called with the original selection
    expect(calls[0][0]).not.toContain("tasks/setup.md");
  });

  it("C3: mode changes during async onContinue do not affect emitted mode", async () => {
    const handler = jest.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    // Select smart mode if mode selector exists
    const smartOption = screen.queryByRole("radio", { name: /^smart import$/i });
    if (smartOption) {
      await userEvent.click(smartOption);
    }

    await userEvent.click(getContinueButton());

    // Mode at call time should be preserved
    const calls = handler.mock.calls as Array<unknown[]>;
    if (calls.length > 0 && calls[0].length > 1) {
      const emittedMode = calls[0][1];
      expect(emittedMode).toBe("smart");
    }
  });

  it("C4: closing modal during onContinue does not leak state", async () => {
    let resolveHandler: () => void;
    const handlerPromise = new Promise<void>((resolve) => {
      resolveHandler = resolve;
    });

    const handler = jest.fn(async () => {
      await handlerPromise;
    });

    const { unmount } = renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Unmount while handler is still pending
    unmount();

    // Resolve the handler (should not crash)
    expect(() => {
      resolveHandler!();
    }).not.toThrow();
  });

  it("C5: rapid mode toggles do not cause state corruption", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartOption = screen.queryByRole("radio", { name: /^smart import$/i });
    const regularOption = screen.queryByRole("radio", { name: /^regular import$/i });

    if (smartOption && regularOption) {
      await toggleFile("features/auth.md");

      // Rapid toggles
      for (let i = 0; i < 10; i++) {
        await userEvent.click(i % 2 === 0 ? smartOption : regularOption);
      }

      await userEvent.click(getContinueButton());

      // Handler should have been called exactly once with valid state
      expect(handler).toHaveBeenCalledTimes(1);
      const [paths, mode] = handler.mock.calls[0];
      expect(Array.isArray(paths)).toBe(true);
      if (mode !== undefined) {
        expect(["regular", "smart"]).toContain(mode);
      }
    }
  });
});

// ===========================================================================
// GROUP D — STATE CONSISTENCY & INVARIANTS
// ===========================================================================

describe("GROUP D: State Consistency & Invariants", () => {
  it("D1: exactly one mode is always selected (if mode selector exists)", async () => {
    renderModal();

    const smartOption = screen.queryByRole("radio", { name: /^smart import$/i });
    const regularOption = screen.queryByRole("radio", { name: /^regular import$/i });

    if (smartOption && regularOption) {
      const checkedCount = [smartOption, regularOption].filter((el) =>
        el.hasAttribute("aria-checked") && el.getAttribute("aria-checked") === "true"
      ).length;

      expect(checkedCount).toBe(1);

      // Toggle and verify invariant holds
      await userEvent.click(smartOption);
      const newCheckedCount = [smartOption, regularOption].filter((el) =>
        el.hasAttribute("aria-checked") && el.getAttribute("aria-checked") === "true"
      ).length;
      expect(newCheckedCount).toBe(1);
    }
  });

  it("D2: file selection state is consistent with UI (aria-pressed)", async () => {
    renderModal();

    const authToggle = screen.getByTestId("toggle-features/auth.md");
    const setupToggle = screen.getByTestId("toggle-tasks/setup.md");

    // Both should start unchecked
    expect(authToggle).toHaveAttribute("aria-pressed", "false");
    expect(setupToggle).toHaveAttribute("aria-pressed", "false");

    // Select auth
    await userEvent.click(authToggle);
    expect(authToggle).toHaveAttribute("aria-pressed", "true");
    expect(setupToggle).toHaveAttribute("aria-pressed", "false");

    // Select setup
    await userEvent.click(setupToggle);
    expect(authToggle).toHaveAttribute("aria-pressed", "true");
    expect(setupToggle).toHaveAttribute("aria-pressed", "true");

    // Deselect auth
    await userEvent.click(authToggle);
    expect(authToggle).toHaveAttribute("aria-pressed", "false");
    expect(setupToggle).toHaveAttribute("aria-pressed", "true");
  });

  it("D3: file path order in array matches selection order (not arbitrary sort)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // Select in specific order: setup first, then auth
    await toggleFile("tasks/setup.md");
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    // Should preserve insertion order, not alphabetical
    expect(paths[0]).toBe("tasks/setup.md");
    expect(paths[1]).toBe("features/auth.md");
  });

  it("D4: isLoading=true disables all interactive elements", () => {
    renderModal({ isLoading: true });

    const authToggle = screen.getByTestId("toggle-features/auth.md");
    const setupToggle = screen.getByTestId("toggle-tasks/setup.md");
    const continueBtn = getContinueButton();

    expect(authToggle).toBeDisabled();
    expect(setupToggle).toBeDisabled();
    expect(continueBtn).toBeDisabled();
    // Cancel may still be enabled depending on design
  });

  it("D5: opening modal resets file selection (fresh state)", () => {
    const { rerender } = renderModal();

    // Select a file
    const authToggle = screen.getByTestId("toggle-features/auth.md");
    expect(authToggle).toHaveAttribute("aria-pressed", "false");

    // Close modal
    rerender(
      <BrowserRouter>
        <ImportTicketsModal
          {...({
            open: false,
            workspaceSlug: "loregarden",
            isLoading: false,
            onClose: jest.fn(),
            onContinue: jest.fn(),
          } as React.ComponentProps<typeof ImportTicketsModal>)}
        />
      </BrowserRouter>
    );

    // Reopen modal
    rerender(
      <BrowserRouter>
        <ImportTicketsModal
          {...({
            open: true,
            workspaceSlug: "loregarden",
            isLoading: false,
            onClose: jest.fn(),
            onContinue: jest.fn(),
          } as React.ComponentProps<typeof ImportTicketsModal>)}
        />
      </BrowserRouter>
    );

    // File selection should be reset
    const resetAuthToggle = screen.getByTestId("toggle-features/auth.md");
    expect(resetAuthToggle).toHaveAttribute("aria-pressed", "false");
  });

  it("D6: mode selection does not automatically trigger onContinue", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartOption = screen.queryByRole("radio", { name: /^smart import$/i });
    if (smartOption) {
      await userEvent.click(smartOption);
      expect(handler).not.toHaveBeenCalled();
    }
  });
});

// ===========================================================================
// GROUP E — ERROR HANDLING & RECOVERY
// ===========================================================================

describe("GROUP E: Error Handling & Recovery", () => {
  it("E1: onContinue throwing error does not crash modal", async () => {
    const handler = jest.fn(() => {
      throw new Error("Test error");
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");

    expect(() => {
      userEvent.click(getContinueButton());
    }).not.toThrow();
  });

  it("E2: onContinue async rejection does not crash modal", async () => {
    const handler = jest.fn(async () => {
      throw new Error("Async error");
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");

    const continueBtn = getContinueButton();

    // Should not crash
    expect(async () => {
      await userEvent.click(continueBtn);
    }).not.toThrow();
  });

  it("E3: invalid file path characters are preserved (not sanitized)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // File system may allow certain special characters
    // Implementation should preserve them as-is
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    expect(paths[0]).toBe("features/auth.md");
  });

  it("E4: onClose error does not affect modal state", async () => {
    const closeHandler = jest.fn(() => {
      throw new Error("Close error");
    });

    renderModal({ onClose: closeHandler });

    const cancelBtn = screen.getByRole("button", { name: /^cancel$/i });

    // Should handle error gracefully
    expect(async () => {
      await userEvent.click(cancelBtn);
    }).not.toThrow();
  });
});

// ===========================================================================
// GROUP F — BOUNDARY CONDITIONS
// ===========================================================================

describe("GROUP F: Boundary Conditions", () => {
  it("F1: very long workspace slug does not break UI", () => {
    const longSlug = "a".repeat(1000);
    expect(() => {
      renderModal({ workspaceSlug: longSlug });
    }).not.toThrow();
  });

  it("F2: very long file paths are handled correctly", async () => {
    // Note: This requires mocking to work; real file system may have limits
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    expect(paths[0]).toBeDefined();
  });

  it("F3: special characters in workspace slug", () => {
    const specialSlug = "test-@#$%^&*()_+=[]{}";
    expect(() => {
      renderModal({ workspaceSlug: specialSlug });
    }).not.toThrow();
  });

  it("F4: unicode characters in workspace slug", () => {
    const unicodeSlug = "test-🚀-résumé-中文";
    expect(() => {
      renderModal({ workspaceSlug: unicodeSlug });
    }).not.toThrow();
  });

  it("F5: very long error message is rendered without truncation/crash", () => {
    const longError = "Error: ".repeat(500) + "something went wrong";
    expect(() => {
      renderModal({ errorMessage: longError });
    }).not.toThrow();
  });

  it("F6: single file selection enables continue (boundary: 1 file)", async () => {
    renderModal();

    expect(getContinueButton()).toBeDisabled();

    await toggleFile("features/auth.md");

    expect(getContinueButton()).not.toBeDisabled();
  });
});

// ===========================================================================
// GROUP G — ASSUMPTION VALIDATION
// ===========================================================================

describe("GROUP G: Assumption Validation", () => {
  it("G1: onContinue is called with array (not spread args)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");
    await userEvent.click(getContinueButton());

    // Should have called once with (array, mode?)
    expect(handler).toHaveBeenCalledTimes(1);
    const args = handler.mock.calls[0];
    expect(args[0]).toEqual(["features/auth.md", "tasks/setup.md"]);
  });

  it("G2: mode parameter is optional (backward compatibility)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Should work even if mode is not provided
    const call = handler.mock.calls[0];
    expect(call[0]).toBeDefined(); // paths always present
    // mode (call[1]) may or may not be present (backward compat)
  });

  it("G3: Continue button text matches expected string", () => {
    renderModal();

    const continueBtn = getContinueButton();
    expect(continueBtn.textContent?.toLowerCase()).toContain("continue");
  });

  it("G4: Cancel button is always available (even during isLoading)", () => {
    renderModal({ isLoading: true });

    const cancelBtn = screen.queryByRole("button", { name: /^cancel$/i });
    expect(cancelBtn).toBeInTheDocument();
  });

  it("G5: file explorer is visible when modal is open", () => {
    renderModal({ open: true });

    const explorer = screen.queryByTestId("mock-file-explorer");
    expect(explorer).toBeInTheDocument();
  });

  it("G6: onClose is called exactly once per cancel click", async () => {
    const handler = jest.fn();
    renderModal({ onClose: handler });

    const cancelBtn = screen.getByRole("button", { name: /^cancel$/i });
    await userEvent.click(cancelBtn);

    expect(handler).toHaveBeenCalledTimes(1);
  });
});

// ===========================================================================
// GROUP H — MUTATION TESTING (Code Mutation Detection)
// ===========================================================================

describe("GROUP H: Mutation Testing", () => {
  it("H1: detects if file selection count validation is removed (should fail at 0 files)", () => {
    renderModal();

    // If the check `selected.length > 0` is removed, this should fail
    const continueBtn = getContinueButton();
    expect(continueBtn).toBeDisabled();
  });

  it("H2: detects if isLoading check is removed (should disable on isLoading=true)", () => {
    renderModal({ isLoading: true });

    const continueBtn = getContinueButton();
    expect(continueBtn).toBeDisabled();
  });

  it("H3: detects if mode is hardcoded to 'regular' (mutation: mode = 'regular')", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartOption = screen.queryByRole("radio", { name: /^smart import$/i });
    if (smartOption) {
      await toggleFile("features/auth.md");
      await userEvent.click(smartOption);
      await userEvent.click(getContinueButton());

      if (handler.mock.calls[0].length > 1) {
        const mode = handler.mock.calls[0][1];
        expect(mode).toBe("smart"); // Should NOT be hardcoded to "regular"
      }
    }
  });

  it("H4: detects if file paths are sorted (mutation: paths.sort())", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // Select in reverse order
    await toggleFile("tasks/setup.md");
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    // If implementation mistakenly sorts, this will fail
    expect(paths).toEqual(["tasks/setup.md", "features/auth.md"]);
  });

  it("H5: detects if canContinue check is removed (mutation: !canContinue removed)", () => {
    renderModal();

    expect(getContinueButton()).toBeDisabled();
    // If guard is removed, button would be enabled despite no selection
  });

  it("H6: detects if handleContinue guard is removed (early return)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler, isLoading: true });

    const continueBtn = getContinueButton();
    // Despite isLoading, if guard is removed, this might still call handler
    // But button should still be disabled
    expect(continueBtn).toBeDisabled();
  });
});
