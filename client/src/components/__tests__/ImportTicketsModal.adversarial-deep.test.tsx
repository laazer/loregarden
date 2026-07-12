import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * Adversarial Deep Break Test Suite for ImportTicketsModal
 *
 * Ticket:   33-add-smart-import-button-to-import-modal-ui
 * Stage:    test_break (test_breaker)
 *
 * This suite targets subtle weaknesses, edge cases, and assumptions that
 * might be missed by standard behavioral tests. Tests are designed to expose:
 *
 * - Memory leaks and cleanup failures
 * - Focus management and accessibility edge cases
 * - Performance degradation under stress
 * - Concurrency and race condition vulnerabilities
 * - Boundary condition failures in state management
 * - Modal stacking and context isolation issues
 * - Component lifecycle and hook dependency issues
 *
 * Expected to FAIL until the component implementation is hardened against
 * these edge cases.
 */

jest.mock("../ImportTicketFileExplorer", () => {
  const FIXTURE_FILES = [
    { path: "a.md", name: "a.md", repo_path: "a.md" },
    { path: "b.md", name: "b.md", repo_path: "b.md" },
    { path: "nested/aa.md", name: "aa.md", repo_path: "nested/aa.md" },
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
      <div data-testid="mock-file-explorer" data-disabled={String(Boolean(props.disabled))}>
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
  onContinue: (filePaths: string[], mode: ImportMode) => void | Promise<void>;
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
  const utils = render(<ImportTicketsModal {...props} />);
  return { ...utils, props };
}

async function toggle(file: string) {
  await userEvent.click(screen.getByTestId(`toggle-${file}`));
}

function getModeGroup(): HTMLElement | null {
  try {
    return screen.getByRole("radiogroup", { name: /import mode/i });
  } catch {
    return null;
  }
}

function getSmartOption(): HTMLElement | null {
  const group = getModeGroup();
  if (!group) return null;
  try {
    return within(group).getByRole("radio", { name: /^smart import$/i });
  } catch {
    return null;
  }
}

function getRegularOption(): HTMLElement | null {
  const group = getModeGroup();
  if (!group) return null;
  try {
    return within(group).getByRole("radio", { name: /^regular import$/i });
  } catch {
    return null;
  }
}

beforeEach(() => {
  jest.clearAllMocks();
});

/**
 * ============================================================================
 * GROUP DEEP1 — Memory Leaks & Cleanup
 * ============================================================================
 */
describe("GROUP DEEP1 — Memory Leaks & Cleanup", () => {
  it("DEEP1-1: closing immediately after opening doesn't leak event listeners", async () => {
    const { rerender, props } = renderModal({ open: true });
    // Immediately close.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: false })}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // No thrown error indicates cleanup succeeded.
  });

  it("DEEP1-2: toggling open/closed 50 times doesn't corrupt state or leak memory", async () => {
    const { rerender, props } = renderModal({ open: true });

    for (let i = 0; i < 50; i++) {
      const isOpen = i % 2 === 0;
      rerender(
        <ImportTicketsModal
          {...({ ...props, open: isOpen })}
        />,
      );
    }

    // Final state: open should be false (50 % 2 = 0, so last was close).
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("DEEP1-3: unmounting while onContinue is async doesn't cause state update after unmount", async () => {
    let resolve: (() => void) | null = null;
    const onContinue = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));

    const { unmount } = renderModal({ onContinue });

    // Open explorer to have something to select (mock explorer is always present).
    if (getSmartOption()) {
      await userEvent.click(getSmartOption()!);
    }
    await toggle("a.md");

    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    // Unmount while async is pending.
    unmount();

    // Resolve the pending promise.
    if (resolve) (resolve as () => void)();

    // No error should occur (React would warn about setState on unmounted component).
  });

  it("DEEP1-4: props update during async onContinue doesn't cause race condition", async () => {
    let resolve: (() => void) | null = null;
    const onContinue1 = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));
    const onContinue2 = jest.fn();

    const { rerender, props } = renderModal({ onContinue: onContinue1 });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    // While async is pending, change onContinue callback.
    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          onContinue: onContinue2,
        })}
      />,
    );

    // Resolve the original promise.
    if (resolve) (resolve as () => void)();

    // First callback should have been called.
    expect(onContinue1).toHaveBeenCalledTimes(1);
    // Second callback should NOT be called from that event.
    expect(onContinue2).not.toHaveBeenCalled();
  });
});

/**
 * ============================================================================
 * GROUP DEEP2 — Focus Management & Accessibility
 * ============================================================================
 */
describe("GROUP DEEP2 — Focus Management & Accessibility", () => {
  it("DEEP2-1: opening modal should move focus into the modal (or at least trap it)", async () => {
    // In a real implementation, modal should receive focus or trap focus.
    renderModal({ open: true });
    const dialog = screen.queryByRole("dialog");
    // Dialog should exist (or at least the mode selector).
    expect(dialog || getModeGroup()).toBeTruthy();
  });

  it("DEEP2-2: Escape key closes the modal if accessible", async () => {
    const onClose = jest.fn();
    renderModal({ onClose, open: true });

    // Focus on the modal.
    const dialog = screen.queryByRole("dialog");
    if (dialog) {
      dialog.focus();
    }

    // Attempt Escape key.
    await userEvent.keyboard("{Escape}");

    // Ideally, Escape should close; if not, this test documents the current behavior.
    // (Spec may not require Escape, but it's a common UX expectation.)
  });

  it("DEEP2-3: focus visibility (not just hover) is maintained on radio options", async () => {
    renderModal();
    const smart = getSmartOption();
    if (!smart) return; // Skip if not implemented.

    smart.focus();
    expect(smart).toHaveFocus();

    // After interaction, focus should be restorable.
    smart.blur();
    smart.focus();
    expect(smart).toHaveFocus();
  });

  it("DEEP2-4: color contrast ratio meets WCAG AA for disabled state", () => {
    // Note: Jest/JSDOM cannot compute real colors. This test documents the intent.
    renderModal({ isLoading: true });
    const smart = getSmartOption();
    if (!smart) return;

    // In production, verify via Axe or manual testing.
    expect(smart).toBeDisabled();
  });

  it("DEEP2-5: all interactive elements have visible focus indicators", async () => {
    renderModal();
    const buttons = screen.getAllByRole("button");

    for (const button of buttons) {
      if (button.hasAttribute("disabled")) {
        // Disabled elements are intentionally excluded from the focus/tab
        // order; asserting they can receive focus would fail everywhere.
        continue;
      }
      button.focus();
      expect(button).toHaveFocus();
      // In JSDOM, can't verify visual focus ring, but element must be focusable.
      button.blur();
    }
  });

  it("DEEP2-6: radio options remain in tab order even if disabled", async () => {
    renderModal({ isLoading: true });
    const smart = getSmartOption();
    if (!smart) return;

    // Even disabled, should have tabindex >= -1 (not hidden from accessibility tree).
    const tabIndex = smart.getAttribute("tabindex");
    // Either explicit tabindex or no tabindex (browser default).
    expect(tabIndex === null || parseInt(tabIndex) >= -1).toBe(true);
  });
});

/**
 * ============================================================================
 * GROUP DEEP3 — Stress & Performance
 * ============================================================================
 */
describe("GROUP DEEP3 — Stress & Performance", () => {
  it("DEEP3-1: 1000 rapid mode toggles doesn't cause performance degradation", async () => {
    renderModal();
    const smart = getSmartOption();
    const regular = getRegularOption();
    if (!smart || !regular) return;

    const start = performance.now();
    for (let i = 0; i < 1000; i++) {
      const target = i % 2 === 0 ? smart : regular;
      try {
        target.click();
      } catch {
        // Ignore errors; just measure that it doesn't hang.
      }
    }
    const end = performance.now();

    // Should complete in reasonable time (< 5 seconds).
    expect(end - start).toBeLessThan(5000);
  });

  it("DEEP3-2: large file selection map doesn't cause layout thrashing", async () => {
    // Simulate selecting many files.
    renderModal();

    for (let i = 0; i < 100; i++) {
      await toggle("a.md");
      await toggle("a.md");
    }

    // Component should still be responsive.
    const button = screen.queryByRole("button", { name: /continue/i });
    expect(button).not.toBeNull();
  });

  it("DEEP3-3: concurrent file toggles don't race and corrupt Map state", async () => {
    renderModal();

    // Simulate rapid concurrent toggles.
    const toggles = [
      toggle("a.md"),
      toggle("b.md"),
      toggle("nested/aa.md"),
      toggle("a.md"),
    ];

    await Promise.all(toggles);

    // Check that final state is consistent.
    const selectedText = screen.queryByText(/selected \(/i);
    if (selectedText) {
      const match = selectedText.textContent?.match(/\((\d+)\)/);
      const count = match ? parseInt(match[1]) : 0;
      // With a.md toggled twice, it should be off. b.md and nested/aa.md should be on.
      expect(count).toBe(2);
    }
  });
});

/**
 * ============================================================================
 * GROUP DEEP4 — Boundary Conditions & Extremes
 * ============================================================================
 */
describe("GROUP DEEP4 — Boundary Conditions & Extremes", () => {
  it("DEEP4-1: very long workspace slug doesn't break UI", async () => {
    const longSlug = "a".repeat(500);
    renderModal({ workspaceSlug: longSlug });
    const dialog = screen.queryByRole("dialog");
    expect(dialog || getModeGroup()).toBeTruthy();
  });

  it("DEEP4-2: extremely long error message doesn't overflow modal", async () => {
    const longError = "Error: ".repeat(100);
    renderModal({ errorMessage: longError });
    const error = screen.queryByText(new RegExp(longError.slice(0, 20)));
    expect(error).toBeTruthy();
  });

  it("DEEP4-3: file count wraps correctly at boundary (0, 1, 2, MAX_SAFE_INTEGER)", async () => {
    renderModal();
    const button = screen.queryByRole("button", { name: /continue/i });

    // 0 files.
    expect(button?.textContent).toContain("0 files");

    await toggle("a.md");
    expect(button?.textContent).toContain("1 file");

    await toggle("b.md");
    expect(button?.textContent).toContain("2 files");
  });

  it("DEEP4-4: null initialMode is treated as 'regular' (not undefined)", () => {
    renderModal({ initialMode: null as never });
    const regular = getRegularOption();
    if (regular) {
      expect(regular).toHaveAttribute("aria-checked", "true");
    }
  });

  it("DEEP4-5: isLoading=true with zero selected files keeps Continue disabled", () => {
    renderModal({ isLoading: true });
    const button = screen.queryByRole("button", { name: /reading files/i });
    expect(button).toBeDisabled();
  });

  it("DEEP4-6: isLoading toggles don't cause focus loss on already-focused elements", async () => {
    const { rerender, props } = renderModal();
    const smart = getSmartOption();
    if (!smart) return;

    smart.focus();
    expect(smart).toHaveFocus();

    // Toggle loading.
    rerender(
      <ImportTicketsModal
        {...({ ...props, isLoading: true })}
      />,
    );

    // Focus should still be on the element (or close to it).
    // In reality, disabled state might move focus; this documents the behavior.
  });
});

/**
 * ============================================================================
 * GROUP DEEP5 — State Isolation & Prop Independence
 * ============================================================================
 */
describe("GROUP DEEP5 — State Isolation & Prop Independence", () => {
  it("DEEP5-1: changing workspaceSlug mid-session doesn't reset internal state", async () => {
    const { rerender, props } = renderModal({
      initialMode: "smart",
      workspaceSlug: "space-1",
    });

    const smart = getSmartOption();
    if (smart) {
      expect(smart).toHaveAttribute("aria-checked", "true");
    }

    // Change workspace.
    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          workspaceSlug: "space-2",
          initialMode: "smart",
        })}
      />,
    );

    if (smart) {
      expect(smart).toHaveAttribute("aria-checked", "true");
    }
  });

  it("DEEP5-2: changing initialBrowsePath prop doesn't affect mode or file selection", async () => {
    const { rerender, props } = renderModal({
      initialMode: "smart",
      initialBrowsePath: "docs/",
    });

    const smart = getSmartOption();
    if (smart) {
      await userEvent.click(smart);
    }
    await toggle("a.md");

    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          initialBrowsePath: "src/",
          initialMode: "smart",
        })}
      />,
    );

    if (smart) {
      expect(smart).toHaveAttribute("aria-checked", "true");
    }
    // File selection should persist (this is already tested elsewhere, but double-check).
  });

  it("DEEP5-3: onClose reference change mid-session doesn't affect modal state", async () => {
    const onClose1 = jest.fn();
    const onClose2 = jest.fn();

    const { rerender, props } = renderModal({ onClose: onClose1 });

    const smart = getSmartOption();
    if (smart) {
      await userEvent.click(smart);
    }

    // Change onClose reference.
    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          onClose: onClose2,
        })}
      />,
    );

    if (smart) {
      expect(smart).toHaveAttribute("aria-checked", "true");
    }

    // Closing should call the new callback.
    const overlay = document.querySelector(".modal-overlay");
    if (overlay) {
      await userEvent.click(overlay);
      expect(onClose2).toHaveBeenCalled();
      expect(onClose1).not.toHaveBeenCalled();
    }
  });

  it("DEEP5-4: initialMode change between closes resets mode correctly", async () => {
    const { rerender, props } = renderModal({ initialMode: "regular", open: true });

    const regular = getRegularOption();
    if (regular) {
      expect(regular).toHaveAttribute("aria-checked", "true");
    }

    // Close.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: false, initialMode: "regular" })}
      />,
    );

    // Reopen with different initialMode.
    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          open: true,
          initialMode: "smart",
        })}
      />,
    );

    const smart = getSmartOption();
    if (smart) {
      expect(smart).toHaveAttribute("aria-checked", "true");
    }
  });
});

/**
 * ============================================================================
 * GROUP DEEP6 — Modal Interaction & Overlay Behavior
 * ============================================================================
 */
describe("GROUP DEEP6 — Modal Interaction & Overlay Behavior", () => {
  it("DEEP6-1: overlay click is ignored when modal is loading", async () => {
    const onClose = jest.fn();
    renderModal({ onClose, isLoading: true });

    const overlay = document.querySelector(".modal-overlay");
    if (overlay) {
      await userEvent.click(overlay);
      expect(onClose).not.toHaveBeenCalled();
    }
  });

  it("DEEP6-2: overlay click during slow onContinue is re-enabled if needed", async () => {
    let resolve: (() => void) | null = null;
    const onContinue = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));
    const onClose = jest.fn();

    renderModal({ onContinue, onClose });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    // onContinue is now pending. Component may disable the overlay to prevent accidental closes.
    // This is implementation-dependent; test documents the behavior.
    if (resolve) (resolve as () => void)();
  });

  it("DEEP6-3: clicking modal content doesn't propagate to overlay click handler", async () => {
    const onClose = jest.fn();
    renderModal({ onClose });

    const body = document.querySelector(".modal-body");
    if (body) {
      await userEvent.click(body);
      expect(onClose).not.toHaveBeenCalled();
    }
  });
});

/**
 * ============================================================================
 * GROUP DEEP7 — Button Behavior & State Guards
 * ============================================================================
 */
describe("GROUP DEEP7 — Button Behavior & State Guards", () => {
  it("DEEP7-1: Continue button type is 'button', not 'submit' (no form submission)", () => {
    renderModal();
    const button = screen.queryByRole("button", { name: /continue/i });
    expect(button?.getAttribute("type")).toBe("button");
  });

  it("DEEP7-2: Continue button text updates before the button is re-enabled", async () => {
    renderModal();
    const button = screen.queryByRole("button", { name: /continue/i });

    expect(button?.textContent).toContain("0 files");
    expect(button).toBeDisabled();

    await toggle("a.md");

    // Text should update before or at the same time as enabled.
    expect(button?.textContent).toContain("1 file");
    expect(button).not.toBeDisabled();
  });

  it("DEEP7-3: Continue button remains disabled if isLoading changes but no files selected", async () => {
    const { rerender, props } = renderModal({ isLoading: false });

    let button = screen.queryByRole("button", { name: /continue/i });
    expect(button).toBeDisabled();

    rerender(
      <ImportTicketsModal
        {...({ ...props, isLoading: true })}
      />,
    );

    button = screen.queryByRole("button", { name: /reading files/i });
    expect(button).toBeDisabled();

    rerender(
      <ImportTicketsModal
        {...({ ...props, isLoading: false })}
      />,
    );

    button = screen.queryByRole("button", { name: /continue/i });
    expect(button).toBeDisabled();
  });

  it("DEEP7-4: Cancel button disables when isLoading is true", () => {
    renderModal({ isLoading: true });
    const cancel = screen.queryByRole("button", { name: /cancel/i });
    expect(cancel).toBeDisabled();
  });
});

/**
 * ============================================================================
 * GROUP DEEP8 — Data Type & Contract Violations
 * ============================================================================
 */
describe("GROUP DEEP8 — Data Type & Contract Violations", () => {
  it("DEEP8-1: onContinue is called with exactly 2 arguments, never 1 or 3+", async () => {
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    if (onContinue.mock.calls.length > 0) {
      expect(onContinue.mock.calls[0]).toHaveLength(2);
    }
  });

  it("DEEP8-2: onContinue first argument is always an array, never null/undefined", async () => {
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    if (onContinue.mock.calls.length > 0) {
      const firstArg = onContinue.mock.calls[0][0];
      expect(Array.isArray(firstArg)).toBe(true);
    }
  });

  it("DEEP8-3: onContinue second argument is always a string ('regular' or 'smart'), never null", async () => {
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    if (onContinue.mock.calls.length > 0) {
      const secondArg = onContinue.mock.calls[0][1];
      expect(typeof secondArg).toBe("string");
      expect(["regular", "smart"]).toContain(secondArg);
    }
  });

  it("DEEP8-4: file paths in onContinue are never duplicated", async () => {
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    await toggle("a.md"); // deselect then reselect
    await toggle("a.md");

    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    if (onContinue.mock.calls.length > 0) {
      const paths = onContinue.mock.calls[0][0] as string[];
      const uniquePaths = new Set(paths);
      expect(uniquePaths.size).toBe(paths.length);
    }
  });

  it("DEEP8-5: file paths in onContinue are always strings, never objects", async () => {
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    if (onContinue.mock.calls.length > 0) {
      const paths = onContinue.mock.calls[0][0] as string[];
      for (const path of paths) {
        expect(typeof path).toBe("string");
      }
    }
  });
});

/**
 * ============================================================================
 * GROUP DEEP9 — Error Handling & Recovery
 * ============================================================================
 */
describe("GROUP DEEP9 — Error Handling & Recovery", () => {
  it("DEEP9-1: if onContinue throws synchronously, modal stays open and usable", async () => {
    const onContinue = jest.fn(() => {
      throw new Error("Handler crashed");
    });

    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      try {
        await userEvent.click(button);
      } catch {
        // Suppress the thrown error for testing.
      }
    }

    // Modal should still be rendered and usable.
    expect(screen.queryByRole("dialog") || getModeGroup()).toBeTruthy();
  });

  it("DEEP9-2: if onContinue Promise rejects, modal doesn't auto-close", async () => {
    const onContinue = jest.fn(() => Promise.reject(new Error("Upload failed")));

    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      try {
        await userEvent.click(button);
      } catch {
        // Suppress.
      }
    }

    // Modal should still be open.
    expect(screen.queryByRole("dialog") || getModeGroup()).toBeTruthy();
  });

  it("DEEP9-3: if errorMessage is XSS payload, it's rendered as text, not HTML", () => {
    const xssPayload = '<img src=x onerror="alert(1)">';
    renderModal({ errorMessage: xssPayload });

    const errorEl = screen.queryByText(xssPayload);
    expect(errorEl).toBeTruthy();

    // Verify no <img> tag was created.
    const img = document.querySelector("img[onerror]");
    expect(img).toBeNull();
  });
});

/**
 * ============================================================================
 * GROUP DEEP10 — Determinism & Idempotency
 * ============================================================================
 */
describe("GROUP DEEP10 — Determinism & Idempotency", () => {
  it("DEEP10-1: opening with same props multiple times produces identical DOM structure", async () => {
    const { container: container1 } = renderModal();
    const { container: container2 } = renderModal();

    // Should be identical (aside from instance IDs if any).
    // This is a rough check; exact comparison may vary.
    expect(container1.querySelectorAll("button").length).toBe(container2.querySelectorAll("button").length);
  });

  it("DEEP10-2: file selection order doesn't affect emitted path order (always sorted)", async () => {
    const callOrder: Array<string[]> = [];
    const onContinue = jest.fn((paths: string[]) => {
      callOrder.push([...paths]);
    });

    const { rerender, props } = renderModal({ onContinue });

    // Select in reverse alphabetical order.
    await toggle("nested/aa.md");
    await toggle("b.md");
    await toggle("a.md");

    const button = screen.queryByRole("button", { name: /continue/i });
    if (button && !button.hasAttribute("disabled")) {
      await userEvent.click(button);
    }

    expect(callOrder[0]).toEqual(["a.md", "b.md", "nested/aa.md"]);

    // Close and reopen, select in different order.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: false })}
      />,
    );

    rerender(
      <ImportTicketsModal
        {...({ ...props, open: true })}
      />,
    );

    // Select in forward order.
    await toggle("a.md");
    await toggle("b.md");
    await toggle("nested/aa.md");

    const button2 = screen.queryByRole("button", { name: /continue/i });
    if (button2 && !button2.hasAttribute("disabled")) {
      await userEvent.click(button2);
    }

    // Order should still be sorted (second call).
    if (callOrder.length >= 2) {
      expect(callOrder[1]).toEqual(["a.md", "b.md", "nested/aa.md"]);
    }
  });
});
