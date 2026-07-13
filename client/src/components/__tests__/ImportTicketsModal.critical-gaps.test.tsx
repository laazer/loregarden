import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * CRITICAL GAPS TEST SUITE — ImportTicketsModal Smart Import Mode
 *
 * Ticket:   33-add-smart-import-button-to-import-modal-ui
 * Stage:    test_break (test_breaker)
 * Purpose:  Expose subtle weaknesses not covered by the main adversarial suite.
 *
 * This suite focuses on:
 * 1. Callback contract violations and timing edge cases
 * 2. Type mismatches and boundary conditions
 * 3. State machine invariant violations
 * 4. Mock interaction gap detection
 * 5. Hook dependency and lifecycle issues
 * 6. API versioning and compatibility edge cases
 *
 * These tests are designed to fail unless the implementation is hardened
 * against these specific failure modes.
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

// ===========================================================================
// GROUP CRITICAL1 — Callback Contract Strictness & Type Safety
// ===========================================================================
describe("GROUP CRITICAL1 — Callback Contract Strictness", () => {
  it("CRITICAL1-1: onContinue MUST receive exactly 2 arguments (paths, mode), not 1", async () => {
    // Guards against regressing to the old single-arg signature.
    // If implementation accidentally calls onContinue(paths) without mode, this fails.
    const callSignatures: any[] = [];
    const strictOnContinue = jest.fn((...args) => {
      callSignatures.push(args);
    });

    renderModal({ onContinue: strictOnContinue });
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(callSignatures.length).toBe(1);
    expect(callSignatures[0].length).toBe(2); // MUST be 2, not 1
    expect(callSignatures[0][1]).toBeDefined(); // mode must exist
    expect(typeof callSignatures[0][1]).toBe("string");
  });

  it("CRITICAL1-2: onContinue mode arg MUST be a string 'regular' or 'smart', not a boolean or enum", async () => {
    // Mutation guard: if implementation passes true/false instead of string, this fails.
    const modeValues: any[] = [];
    const strictOnContinue = jest.fn((_paths, mode) => {
      modeValues.push(mode);
    });

    renderModal({ onContinue: strictOnContinue, initialMode: "smart" });
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(modeValues[0]).toBe("smart"); // exactly "smart", not 1 or true
    expect(typeof modeValues[0]).toBe("string");
  });

  it("CRITICAL1-3: initialMode must NOT affect the emitted mode if user changes it", async () => {
    // If initialMode is "smart" but user clicks Regular before Continue, mode must be "regular".
    const emittedModes: ImportMode[] = [];
    const onContinue = jest.fn((_paths, mode) => {
      emittedModes.push(mode);
    });

    renderModal({ initialMode: "smart", onContinue });

    const smart = getSmartOption();
    expect(smart).toHaveAttribute("aria-checked", "true");

    // User changes to regular.
    await userEvent.click(getRegularOption()!);
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Emitted mode must reflect user's choice, not initialMode.
    expect(emittedModes[0]).toBe("regular");
  });

  it("CRITICAL1-4: onContinue receives the CURRENT mode at call time, not a stale closure", async () => {
    // Closure bug: if mode is captured in a stale closure, changing it then clicking
    // Continue will emit the old mode. This test ensures the implementation captures
    // the live state at invoke time.
    const modeAtCallTime: ImportMode[] = [];
    const onContinue = jest.fn((_paths, mode) => {
      modeAtCallTime.push(mode);
    });

    const { rerender, props } = renderModal({ onContinue });

    await toggle("a.md");

    // Switch to smart.
    await userEvent.click(getSmartOption()!);
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    // Now rerender with a fresh handler (simulating parent prop change).
    const newOnContinue = jest.fn((_paths, mode) => {
      modeAtCallTime.push(mode);
    });

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: newOnContinue,
        })}
      />,
    );

    // Click Continue — the NEW handler must receive the current mode (smart).
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(newOnContinue).toHaveBeenCalledTimes(1);
    expect(newOnContinue).toHaveBeenCalledWith(["a.md"], "smart");
  });

  it("CRITICAL1-5: onContinue receives pathS array (plural), not a single path string", async () => {
    // Type guard: mode arg is string, but paths must remain an array.
    const receivedPaths: any[] = [];
    const onContinue = jest.fn((paths, _mode) => {
      receivedPaths.push(paths);
    });

    renderModal({ onContinue });
    await toggle("a.md");
    await toggle("b.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(receivedPaths[0]).toBeDefined();
    expect(Array.isArray(receivedPaths[0])).toBe(true);
    expect(receivedPaths[0].length).toBe(2);
    expect(receivedPaths[0].every((p: any) => typeof p === "string")).toBe(true);
  });
});

// ===========================================================================
// GROUP CRITICAL2 — Mode State Machine Invariants
// ===========================================================================
describe("GROUP CRITICAL2 — Mode State Machine Invariants", () => {
  it("CRITICAL2-1: initialMode and rendered checked state MUST be in sync on mount", () => {
    // If component renders but initialMode is ignored, tests fail.
    renderModal({ initialMode: "smart" });
    const smart = getSmartOption();
    const regular = getRegularOption();

    expect(smart).not.toBeNull();
    expect(regular).not.toBeNull();

    // MUST be in sync with initialMode.
    expect(smart).toHaveAttribute("aria-checked", "true");
    expect(regular).toHaveAttribute("aria-checked", "false");
  });

  it("CRITICAL2-2: changing initialMode and re-opening MUST reset to the new default", async () => {
    // Mutation guard: ensure close+reopen with different initialMode actually changes the default.
    const { rerender, props } = renderModal({ initialMode: "regular" });

    await userEvent.click(getSmartOption()!);
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    // Close.
    rerender(
      <ImportTicketsModal
        {...({
          open: false,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "regular",
        })}
      />,
    );

    // Reopen with DIFFERENT initialMode.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart", // CHANGED
        })}
      />,
    );

    // Must reset to new default (smart).
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "false");
  });

  it("CRITICAL2-3: mode is NOT affected by workspaceSlug changes (prop unrelated to mode)", async () => {
    // Guards against accidental coupling: workspaceSlug changes should NOT reset mode.
    const { rerender, props } = renderModal({ initialMode: "smart" });

    await userEvent.click(getSmartOption()!);
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    // Change only workspaceSlug.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "OTHER-SLUG", // CHANGED
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    // Mode must NOT change just because workspaceSlug changed.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL2-4: mode is NOT affected by initialBrowsePath changes", async () => {
    // Same guard as CRITICAL2-3 but for initialBrowsePath.
    const { rerender, props } = renderModal({
      initialMode: "smart",
      initialBrowsePath: "docs/",
    });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          initialBrowsePath: "src/", // CHANGED
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    // Mode survives the browsePath change.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL2-5: exactly one mode is always selected, never zero or two", async () => {
    // Invariant: the radio group must always maintain exactly one selection.
    // This guards against state bugs where both/neither are checked.
    renderModal();

    for (let i = 0; i < 20; i++) {
      const radios = screen.getAllByRole("radio");
      const checked = radios.filter((r) => r.getAttribute("aria-checked") === "true");

      expect(checked.length).toBe(1); // ALWAYS exactly 1

      // Toggle and check again.
      const toClick = i % 2 === 0 ? getSmartOption() : getRegularOption();
      if (toClick) {
        await userEvent.click(toClick);
      }
    }
  });
});

// ===========================================================================
// GROUP CRITICAL3 — File Selection & Mode Coupling Edge Cases
// ===========================================================================
describe("GROUP CRITICAL3 — File Selection & Mode Decoupling", () => {
  it("CRITICAL3-1: file selection is NOT cleared when mode changes", async () => {
    // Regression guard: mode changes must never trigger file deselection.
    renderModal();

    // Select files in regular mode.
    await toggle("a.md");
    await toggle("b.md");
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();

    // Switch to smart.
    await userEvent.click(getSmartOption()!);

    // Files must still be selected.
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();
  });

  it("CRITICAL3-2: selecting files in regular mode, switching to smart, and emitting has correct order", async () => {
    // Ensures that mode switching doesn't corrupt the path order or the mode value.
    const callOrder: Array<[string[], ImportMode]> = [];
    const onContinue = jest.fn((paths, mode) => {
      callOrder.push([paths, mode]);
    });

    renderModal({ onContinue });

    // Select in regular mode.
    await toggle("b.md");
    await toggle("a.md");

    // Switch to smart.
    await userEvent.click(getSmartOption()!);

    // Continue.
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(callOrder[0][0]).toEqual(["a.md", "b.md"]); // sorted
    expect(callOrder[0][1]).toBe("smart"); // mode reflects the switch
  });

  it("CRITICAL3-3: deselecting all files while in smart mode disables Continue", async () => {
    // Guards against mode-based conditional disable logic breaking.
    renderModal({ initialMode: "smart" });

    await toggle("a.md");
    expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();

    await toggle("a.md"); // deselect
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();

    // Mode is still smart, but Continue stays disabled (controlled by file count, not mode).
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL3-4: rapidly toggling files and modes doesn't corrupt either state", async () => {
    // Stress test: mode and file selection states are independent.
    renderModal({ initialMode: "regular" });

    for (let i = 0; i < 30; i++) {
      if (i % 3 === 0) {
        await toggle("a.md");
      } else if (i % 3 === 1) {
        await toggle("b.md");
      } else {
        const which = i % 2 === 0 ? getSmartOption() : getRegularOption();
        if (which) await userEvent.click(which);
      }
    }

    // Both states should be coherent.
    const smart = getSmartOption();
    const regular = getRegularOption();
    const checked = [smart, regular].filter(
      (r) => r && r.getAttribute("aria-checked") === "true"
    );
    expect(checked.length).toBe(1);

    // Files still accessible.
    expect(screen.getByTestId("toggle-a.md")).toBeInTheDocument();
  });
});

// ===========================================================================
// GROUP CRITICAL4 — Loading State & Mode Interaction
// ===========================================================================
describe("GROUP CRITICAL4 — Loading State & Mode Interaction", () => {
  it("CRITICAL4-1: during isLoading, mode options are disabled but mode state is NOT reset", async () => {
    // Ensures the disabled state doesn't corrupt the live mode value.
    const { rerender, props } = renderModal({ initialMode: "smart", isLoading: false });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    // Switch to loading.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: true,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    expect(getSmartOption()).toBeDisabled();
    // But the CHECKED state must NOT change.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL4-2: after isLoading transitions back to false, mode is still selectable", async () => {
    // Guards against disabled state being "sticky" or not properly re-enabling.
    const { rerender, props } = renderModal({ isLoading: true });

    const smart = getSmartOption();
    expect(smart).toBeDisabled();

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    // Must be clickable now.
    expect(getSmartOption()).not.toBeDisabled();
    await userEvent.click(getSmartOption()!);
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL4-3: clicking Continue while isLoading=true does not call onContinue", async () => {
    // Ensures isLoading prevents emissions (double-guard with disabled button).
    const onContinue = jest.fn();
    renderModal({ isLoading: true, onContinue });

    await toggle("a.md");
    const button = screen.getByRole("button", { name: /reading files/i });

    // Button is disabled; direct click via button.click() should also not fire.
    try {
      button.click();
    } catch {
      // ignore
    }

    expect(onContinue).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// GROUP CRITICAL5 — Error Message & Mode State Coupling
// ===========================================================================
describe("GROUP CRITICAL5 — Error Message & Mode Stability", () => {
  it("CRITICAL5-1: errorMessage appearing does NOT reset or change mode", async () => {
    // Ensures mode is not accidentally bound to error state.
    const { rerender, props } = renderModal({ initialMode: "smart", errorMessage: null });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          errorMessage: "Network error",
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    expect(screen.getByText("Network error")).toBeInTheDocument();
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL5-2: errorMessage disappearing does NOT reset mode", async () => {
    // Same as CRITICAL5-1 but in reverse.
    const { rerender, props } = renderModal({
      initialMode: "smart",
      errorMessage: "Error",
    });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          errorMessage: null,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    expect(screen.queryByText("Error")).not.toBeInTheDocument();
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL5-3: errorMessage content changes do not affect mode", async () => {
    // Ensures even swapping the error message doesn't corrupt mode.
    const { rerender, props } = renderModal({
      initialMode: "smart",
      errorMessage: "Error A",
    });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          errorMessage: "Error B",
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    expect(screen.getByText("Error B")).toBeInTheDocument();
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });
});

// ===========================================================================
// GROUP CRITICAL6 — Callback Promise/Async Handling with Mode
// ===========================================================================
describe("GROUP CRITICAL6 — Callback Promise & Mode Consistency", () => {
  it("CRITICAL6-1: if onContinue is slow async, mode remains accessible and consistent", async () => {
    // Ensures async operations don't disrupt mode state.
    let resolve: () => void = () => {};
    const onContinue = jest.fn(
      () =>
        new Promise<void>((res) => {
          resolve = res;
        })
    );

    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });

    await userEvent.click(button);
    expect(onContinue).toHaveBeenCalledTimes(1);

    // While async is pending, user tries to switch modes.
    const smart = getSmartOption();
    expect(smart).toHaveAttribute("aria-checked", "false");

    await userEvent.click(smart!);
    expect(smart).toHaveAttribute("aria-checked", "true");

    // Resolve the pending async.
    resolve();

    // Mode should still be smart (async didn't interfere).
    expect(smart).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL6-2: if onContinue throws, mode is NOT reset or corrupted", async () => {
    const throwingOnContinue = jest.fn(() => {
      throw new Error("Handler failed");
    });

    renderModal({ onContinue: throwingOnContinue });
    const smart = getSmartOption();

    await userEvent.click(smart!);
    expect(smart).toHaveAttribute("aria-checked", "true");

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(throwingOnContinue).toHaveBeenCalledTimes(1);

    // Mode should NOT be corrupted by the throw.
    expect(smart).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL6-3: if onContinue is a rejected Promise, mode is NOT affected", async () => {
    const rejectingOnContinue = jest.fn(() => Promise.reject(new Error("Upload failed")));

    renderModal({ onContinue: rejectingOnContinue });
    const smart = getSmartOption();

    await userEvent.click(smart!);
    expect(smart).toHaveAttribute("aria-checked", "true");

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Rejection happened but mode is intact.
    expect(smart).toHaveAttribute("aria-checked", "true");
  });
});

// ===========================================================================
// GROUP CRITICAL7 — Backward Compatibility & API Contract
// ===========================================================================
describe("GROUP CRITICAL7 — Backward Compatibility", () => {
  it("CRITICAL7-1: old single-arg callers (onContinue(paths)) still work", async () => {
    // Additive change: new mode arg should not break old callers.
    const received: string[][] = [];
    const legacyOnContinue = (paths: string[]) => {
      received.push(paths);
    };

    renderModal({ onContinue: legacyOnContinue as never });

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(received).toEqual([["a.md"]]);
  });

  it("CRITICAL7-2: mode prop does NOT appear in onClose callback", async () => {
    // Ensures mode is only passed to onContinue, never onClose.
    const onClose = jest.fn();
    renderModal({ onClose });

    const overlay = document.querySelector(".modal-overlay") as HTMLElement;
    await userEvent.click(overlay);

    // onClose called with no args (or undefined).
    expect(onClose).toHaveBeenCalledTimes(1);
    // Calling convention for onClose should not change.
    expect(onClose.mock.calls[0].length).toBe(0);
  });

  it("CRITICAL7-3: if onContinue callback is replaced mid-interaction, new callback receives correct mode", async () => {
    // Ensures callback substitution doesn't break the mode contract.
    const onContinue1 = jest.fn();
    const { rerender, props } = renderModal({ onContinue: onContinue1 });

    const smart = getSmartOption();
    await userEvent.click(smart!);

    // Replace callback.
    const onContinue2 = jest.fn();
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: onContinue2,
        })}
      />,
    );

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // New callback should receive the current mode (smart).
    expect(onContinue2).toHaveBeenCalledWith(["a.md"], "smart");
  });
});

// ===========================================================================
// GROUP CRITICAL8 — Accessibility Contract & ARIA Invariants
// ===========================================================================
describe("GROUP CRITICAL8 — Accessibility Invariants", () => {
  it("CRITICAL8-1: radiogroup role is never removed, even across renders", async () => {
    const { rerender, props } = renderModal();

    const getModeGroupRole = () => getModeGroup()?.getAttribute("role");

    expect(getModeGroupRole()).toBe("radiogroup");

    // Rerender with prop changes.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "other-workspace",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    expect(getModeGroupRole()).toBe("radiogroup");
  });

  it("CRITICAL8-2: both radio elements have role='radio', not role='button'", () => {
    renderModal();

    const smart = getSmartOption();
    const regular = getRegularOption();

    expect(smart?.getAttribute("role")).toBe("radio");
    expect(regular?.getAttribute("role")).toBe("radio");
  });

  it("CRITICAL8-3: aria-describedby references must be stable and non-null", () => {
    renderModal();

    const smart = getSmartOption();
    const describedById = smart?.getAttribute("aria-describedby");

    expect(describedById).toBeTruthy();
    expect(describedById?.trim().length).toBeGreaterThan(0);

    const description = document.getElementById(describedById || "");
    expect(description).not.toBeNull();
  });

  it("CRITICAL8-4: aria-label on radiogroup must contain 'import' and 'mode' (case-insensitive)", () => {
    renderModal();

    const group = getModeGroup();
    const ariaLabel = group?.getAttribute("aria-label") || "";

    expect(ariaLabel.toLowerCase()).toMatch(/import.*mode|mode.*import/);
  });
});

// ===========================================================================
// GROUP CRITICAL9 — Edge Case: Prop Type Mismatches
// ===========================================================================
describe("GROUP CRITICAL9 — Type Safety & Boundary Conditions", () => {
  it("CRITICAL9-1: initialMode with invalid values defaults to 'regular' or errors explicitly", () => {
    renderModal({ initialMode: "invalid" as never });

    const smart = getSmartOption();
    const regular = getRegularOption();

    // One must be checked (not the invalid mode).
    const checked = [smart, regular].filter((r) => r?.getAttribute("aria-checked") === "true");
    expect(checked.length).toBe(1);
  });

  it("CRITICAL9-2: empty string initialMode defaults to 'regular'", () => {
    renderModal({ initialMode: "" as never });

    // Should NOT crash; must default to regular.
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL9-3: null initialMode defaults to 'regular'", () => {
    renderModal({ initialMode: null as never });

    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("CRITICAL9-4: undefined initialMode defaults to 'regular'", () => {
    renderModal({ initialMode: undefined });

    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });
});

// ===========================================================================
// GROUP CRITICAL10 — State Persistence Across Lifecycle Events
// ===========================================================================
describe("GROUP CRITICAL10 — Lifecycle & Persistence", () => {
  it("CRITICAL10-1: mode state survives multiple rapid open/close cycles", async () => {
    const { rerender, props } = renderModal({ initialMode: "smart" });

    for (let cycle = 0; cycle < 3; cycle++) {
      if (cycle > 0) {
        // Re-open.
        rerender(
          <ImportTicketsModal
            {...({ ...props, open: true })}
          />,
        );
      }

      const smart = getSmartOption();
      // On re-open, should reset to initialMode (smart).
      expect(smart).toHaveAttribute("aria-checked", "true");

      // Close.
      rerender(
        <ImportTicketsModal
          {...({ ...props, open: false })}
        />,
      );
    }
  });

  it("CRITICAL10-2: mode persists across errorMessage changes without reset", async () => {
    const { rerender, props } = renderModal({ initialMode: "smart" });

    // Change error multiple times.
    for (const error of ["Error A", "Error B", "Error C", null]) {
      rerender(
        <ImportTicketsModal
          {...({
            open: true,
            workspaceSlug: "loregarden",
            isLoading: false,
            errorMessage: error,
            onClose: props.onClose,
            onContinue: props.onContinue,
            initialMode: "smart",
          })}
        />,
      );

      // Mode must remain smart through all error changes.
      expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    }
  });

  it("CRITICAL10-3: mode persists across isLoading true/false transitions without reset", async () => {
    const { rerender, props } = renderModal({ initialMode: "smart" });

    for (let i = 0; i < 5; i++) {
      const isLoading = i % 2 === 0;
      rerender(
        <ImportTicketsModal
          {...({
            open: true,
            workspaceSlug: "loregarden",
            isLoading,
            onClose: props.onClose,
            onContinue: props.onContinue,
            initialMode: "smart",
          })}
        />,
      );

      // Mode must persist through loading state changes.
      expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    }
  });
});
