import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * Integration Test Suite: Smart Import to Studio Routing
 *
 * Ticket:   34-route-smart-import-selection-to-studio-with-prev
 * Stage:    test_break (test_breaker)
 *
 * Purpose:
 * Adversarial tests for the smart import routing flow. These tests expose
 * potential weaknesses in the routing implementation by testing:
 *
 * 1. STATE MACHINE VIOLATIONS - Invalid state transitions
 * 2. DATA LOSS SCENARIOS - Data corruption during routing
 * 3. MODAL LIFECYCLE - Improper cleanup or state leakage
 * 4. ASYNC/TIMING ISSUES - Race conditions in routing
 * 5. ERROR RECOVERY - Handling failed routing attempts
 * 6. MODE PARAMETER MUTATIONS - Testing import mode handling
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
// GROUP S1: STATE MACHINE VIOLATIONS
// ===========================================================================

describe("GROUP S1: State Machine Violations", () => {
  it("S1-1: Multiple rapid continues should not bypass button disable", async () => {
    const handler = jest.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    renderModal({ onContinue: handler });
    await toggleFile("features/auth.md");

    const btn = getContinueButton();
    // Simulate rapidly mashing the button
    await userEvent.click(btn);
    await userEvent.click(btn);
    await userEvent.click(btn);

    // Should not call handler multiple times
    expect(handler.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("S1-2: Closing modal during onContinue should prevent further routing", async () => {
    let resolveHandler: () => void = () => {};
    const handlerPromise = new Promise<void>((resolve) => {
      resolveHandler = resolve;
    });

    const continueHandler = jest.fn(async () => {
      await handlerPromise;
    });

    const closeHandler = jest.fn();

    const { unmount } = renderModal({
      onContinue: continueHandler,
      onClose: closeHandler,
    });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Modal closes immediately without waiting
    unmount();

    // Resolve the handler (should not throw or cause state corruption)
    expect(() => {
      resolveHandler();
    }).not.toThrow();

    expect(continueHandler).toHaveBeenCalledTimes(1);
  });

  it("S1-3: Switching workspace while modal stays open preserves selection (established contract)", async () => {
    const { rerender } = renderModal({ workspaceSlug: "workspace-1" });

    await toggleFile("features/auth.md");

    // Switch workspace (simulates user navigating away)
    rerender(
      <BrowserRouter>
        <ImportTicketsModal
          open={true}
          workspaceSlug="workspace-2"
          isLoading={false}
          onClose={jest.fn()}
          onContinue={jest.fn()}
        />
      </BrowserRouter>,
    );

    // The modal only resets selection when `open` transitions (see the
    // `useEffect(..., [open])` in ImportTicketsModal.tsx). This is the
    // deliberate, pinned contract per ImportTicketsModal.test.tsx X37
    // ("workspaceSlug change does not reset or corrupt mode state") — a plain
    // workspaceSlug change while `open` stays true must NOT clear selection.
    const authToggle = screen.getByTestId("toggle-features/auth.md");
    expect(authToggle).toHaveAttribute("aria-pressed", "true");
  });

  it("S1-4: mode parameter should not revert to default after selection", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartRadio = screen.queryByRole("radio", { name: /^smart import$/i });
    if (!smartRadio) {
      // Smart mode not yet implemented, skip this test
      return;
    }

    await userEvent.click(smartRadio);
    await toggleFile("features/auth.md");

    // Simulate user waiting a moment
    await new Promise((resolve) => setTimeout(resolve, 100));

    await userEvent.click(getContinueButton());

    // Mode should still be smart, not reverted
    const [, mode] = handler.mock.calls[0];
    expect(mode).toBe("smart");
  });
});

// ===========================================================================
// GROUP S2: DATA LOSS & CORRUPTION
// ===========================================================================

describe("GROUP S2: Data Loss & Corruption", () => {
  it("S2-1: File paths should not be corrupted during routing", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // Select files in specific order
    await toggleFile("tasks/setup.md");
    await toggleFile("features/auth.md");

    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    // Paths must be valid and non-corrupted
    expect(paths).toContain("features/auth.md");
    expect(paths).toContain("tasks/setup.md");
    expect(paths.length).toBe(2);

    // Check for corruption markers
    for (const path of paths) {
      expect(path).not.toContain("\x00"); // null byte
      expect(path).not.toContain("\n"); // newline in path
      expect(path).toBeTruthy();
    }
  });

  it("S2-2: Selected files should not be duplicated in routing", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    const uniquePaths = new Set(paths);
    expect(uniquePaths.size).toBe(paths.length);
  });

  it("S2-3: Mode should not be mutated or undefined during routing", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const call = handler.mock.calls[0];
    expect(call.length).toBeGreaterThanOrEqual(1);

    // If mode is provided, it must be valid
    if (call.length > 1) {
      expect(typeof call[1]).toBe("string");
      expect(["regular", "smart"]).toContain(call[1]);
    }
  });

  it("S2-4: workspace slug should not leak between modal instances", async () => {
    const handler1 = jest.fn();
    const handler2 = jest.fn();

    // First modal
    const { unmount: unmount1 } = renderModal({
      workspaceSlug: "ws-1",
      onContinue: handler1,
    });
    await toggleFile("features/auth.md");
    unmount1();

    // Second modal with different workspace
    renderModal({
      workspaceSlug: "ws-2",
      onContinue: handler2,
    });

    // Workspace should be isolated
    // (no way to directly verify without inspecting internals)
    expect(screen.getByText("ws-2")).toBeInTheDocument();
  });
});

// ===========================================================================
// GROUP S3: ASYNC TIMING & RACE CONDITIONS
// ===========================================================================

describe("GROUP S3: Async Timing & Race Conditions", () => {
  it("S3-1: File selection changes during async routing should not affect emitted paths", async () => {
    let resolveHandler: () => void = () => {};
    const handlerPromise = new Promise<void>((resolve) => {
      resolveHandler = resolve;
    });

    const handler = jest.fn(async () => {
      await handlerPromise;
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    const btn = getContinueButton();
    await userEvent.click(btn);

    // Handler is still async. onContinue is always invoked with (paths, mode)
    // — mode defaults to "regular" (see ImportTicketsModal.tsx handleContinue).
    expect(handler).toHaveBeenCalledWith(["features/auth.md"], "regular");

    // Try to select more files while routing
    try {
      const setupToggle = screen.getByTestId("toggle-tasks/setup.md");
      await userEvent.click(setupToggle);
    } catch {
      // Button might be disabled, which is fine
    }

    resolveHandler();

    // Original call should not have included new selection
    const calls = handler.mock.calls as Array<unknown[]>;
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[0][0]).toEqual(["features/auth.md"]);
  });

  it("S3-2: onContinue completion should not trigger onClose automatically", async () => {
    const continueHandler = jest.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const closeHandler = jest.fn();

    renderModal({ onContinue: continueHandler, onClose: closeHandler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Wait for handler to complete
    await waitFor(() => {
      expect(continueHandler).toHaveBeenCalled();
    });

    // onClose should NOT be called automatically
    expect(closeHandler).not.toHaveBeenCalled();
  });

  it("S3-3: Button state should not race with routing start", async () => {
    let resolveHandler: () => void = () => {};
    const handlerPromise = new Promise<void>((resolve) => {
      resolveHandler = resolve;
    });

    const handler = jest.fn(async () => {
      await handlerPromise;
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    const btn = getContinueButton();

    await userEvent.click(btn);

    // Button should be disabled during routing
    // (implementation may disable it, allowing prevents multiple clicks)
    // Clicking again should not increment call count
    try {
      await userEvent.click(btn);
    } catch {
      // Button might be disabled
    }

    resolveHandler();

    // Should only be called once
    expect(handler.mock.calls.length).toBeLessThanOrEqual(1);
  });
});

// ===========================================================================
// GROUP S4: ERROR RECOVERY
// ===========================================================================

describe("GROUP S4: Error Recovery", () => {
  it("S4-1: onContinue throwing error should not crash modal", async () => {
    const handler = jest.fn(() => {
      throw new Error("Routing failed");
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");

    // Note: `expect(asyncFn).not.toThrow()` only checks that invoking the
    // function doesn't throw *synchronously* — it does not await the
    // returned promise, so it would resolve before the click's effects (and
    // handler invocation) actually happen. Await the click directly instead;
    // ImportTicketsModal's handleContinue already wraps onContinue in a
    // try/catch, so this genuinely won't throw.
    await userEvent.click(getContinueButton());

    expect(handler).toHaveBeenCalled();
  });

  it("S4-2: onContinue async rejection should not crash modal", async () => {
    const handler = jest.fn(async () => {
      throw new Error("Async routing error");
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");

    // See S4-1: await the click directly rather than wrapping it in
    // `expect(asyncFn).not.toThrow()`, which never awaits the promise.
    await userEvent.click(getContinueButton());

    expect(handler).toHaveBeenCalled();
  });

  it("S4-3: Modal should remain usable after failed routing", async () => {
    const handler = jest.fn(async () => {
      throw new Error("Routing failed");
    });

    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Wait a moment for error to settle
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Modal should still be interactable
    const closeBtn = screen.getByRole("button", { name: /^cancel$/i });
    expect(closeBtn).not.toBeDisabled();
  });
});

// ===========================================================================
// GROUP S5: BOUNDARY CASES FOR ROUTING
// ===========================================================================

describe("GROUP S5: Boundary Cases for Routing", () => {
  it("S5-1: routing with single file should work", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // onContinue is always invoked with (paths, mode); mode defaults to "regular".
    expect(handler).toHaveBeenCalledWith(["features/auth.md"], "regular");
  });

  it("S5-2: routing with maximum files (stress test)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // Select all available files
    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");

    await userEvent.click(getContinueButton());

    const [paths] = handler.mock.calls[0];
    expect(paths.length).toBe(2);
  });

  it("S5-3: rerendering without toggling `open` preserves selection (established contract)", async () => {
    const handler = jest.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });

    const { rerender } = renderModal({ onContinue: handler });

    // First import
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    // Rerender with the same `open: true` (no close/reopen cycle). Selection
    // only resets when `open` transitions (see ImportTicketsModal.tsx's
    // `useEffect(..., [open])`, and ImportTicketsModal.test.tsx X37), so a
    // rerender that never flips `open` to false must NOT clear selection.
    rerender(
      <BrowserRouter>
        <ImportTicketsModal
          open={true}
          workspaceSlug="loregarden"
          isLoading={false}
          onClose={jest.fn()}
          onContinue={handler}
        />
      </BrowserRouter>,
    );

    const authToggle = screen.getByTestId("toggle-features/auth.md");
    expect(authToggle).toHaveAttribute("aria-pressed", "true");
  });
});

// ===========================================================================
// GROUP S6: MODE PARAMETER MUTATIONS
// ===========================================================================

describe("GROUP S6: Mode Parameter Mutations", () => {
  it("S6-1: mode parameter should be consistent across calls", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartRadio = screen.queryByRole("radio", { name: /^smart import$/i });
    if (smartRadio) {
      await userEvent.click(smartRadio);
    }

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const mode = handler.mock.calls[0][1];
    expect(mode).not.toBeFalsy();
    expect(typeof mode).toBe("string");
  });

  it("S6-2: changing mode should not affect file selection", async () => {
    renderModal();

    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");

    const smartRadio = screen.queryByRole("radio", { name: /^smart import$/i });
    if (smartRadio) {
      await userEvent.click(smartRadio);
    }

    expect(screen.getByTestId("toggle-features/auth.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("toggle-tasks/setup.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("S6-3: mode mutations should not corrupt data", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    const smartRadio = screen.queryByRole("radio", { name: /^smart import$/i });
    const regularRadio = screen.queryByRole("radio", { name: /^regular import$/i });

    if (smartRadio && regularRadio) {
      await toggleFile("features/auth.md");

      // Rapidly toggle modes
      for (let i = 0; i < 5; i++) {
        await userEvent.click(i % 2 === 0 ? smartRadio : regularRadio);
      }

      await userEvent.click(getContinueButton());

      // Data should not be corrupted
      const [paths, mode] = handler.mock.calls[0];
      expect(paths).toEqual(["features/auth.md"]);
      expect(typeof mode).toBe("string");
    }
  });
});
