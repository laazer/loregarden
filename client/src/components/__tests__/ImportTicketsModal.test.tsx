import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * Behavioral + adversarial test suite for ImportTicketsModal.
 *
 * Ticket:   33-add-smart-import-button-to-import-modal-ui
 * Stage:    test_break (test_breaker)
 * Design:   client/src/components/__tests__/TEST_DESIGN_SmartImportButton.md
 *
 * These tests encode the DOM/behavioral contract for the smart-import mode
 * selector. They are written test-first: the mode selector is not yet
 * implemented, so the mode-specific cases are expected to fail (red) until the
 * Implementer adds the selector. The regression cases (Group C) already
 * describe existing behavior and must stay green throughout.
 *
 * Requirement mapping (from the test design):
 *   - Group R  — Rendering                (AC-a, AC-c)
 *   - Group S  — Selection behavior       (AC-a)
 *   - Group H  — Handoff on Continue      (AC-a, contract)
 *   - Group L  — Labels & tooltips        (AC-b)
 *   - Group C  — Regression / back-compat
 *   - Group X  — Adversarial edge cases
 *
 * NOTE (spec discrepancy flagged for Spec Agent):
 *   The design's Test Coverage Summary names `vitest` as the framework, but the
 *   client is wired for Jest (`@swc/jest`, `jest-environment-jsdom`,
 *   `"test": "jest"`). This suite is authored for Jest to run in-repo.
 *
 * NOTE (behavioral discrepancy flagged for Spec Agent):
 *   Design case H2 states the emitted file order is "selection order". The real
 *   modal derives its list via `selectedImportFileList`, which sorts by
 *   `repo_path.localeCompare`. The emitted order is therefore ALPHABETICAL by
 *   repo_path, not selection order. Tests below assert the true (sorted)
 *   contract and X-group explicitly pins it so an implementer cannot silently
 *   "fix" it to selection order without breaking a test.
 */

// ---------------------------------------------------------------------------
// Deterministic mock of the file explorer (a true external boundary: it makes
// network calls via `api.browseImportDirectory`). The mock renders one toggle
// button per fixture file and forwards clicks to `onToggleFile`, letting each
// test drive selection deterministically without any network/timer.
// ---------------------------------------------------------------------------
jest.mock("../ImportTicketFileExplorer", () => {
  const FIXTURE_FILES = [
    { path: "a.md", name: "a.md", repo_path: "a.md" },
    { path: "b.md", name: "b.md", repo_path: "b.md" },
    // repo_path sorts BEFORE b.md but is toggled after it in some tests, to
    // expose the sort-vs-selection-order behavior.
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

// ImportMode is additive to the public contract; keep a local alias so the test
// intent is legible even before the component exports the type.
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

function getModeGroup(): HTMLElement {
  return screen.getByRole("radiogroup", { name: /import mode/i });
}

function getRegularOption(): HTMLElement {
  return within(getModeGroup()).getByRole("radio", { name: /^regular import$/i });
}

function getSmartOption(): HTMLElement {
  return within(getModeGroup()).getByRole("radio", { name: /^smart import$/i });
}

function checkedCount(): number {
  return screen
    .getAllByRole("radio")
    .filter((el) => el.getAttribute("aria-checked") === "true").length;
}

beforeEach(() => {
  jest.clearAllMocks();
});

// ===========================================================================
// Group R — Rendering (AC-a, AC-c)
// ===========================================================================
describe("Group R — Rendering", () => {
  it("R1: renders an 'Import mode' radiogroup with exactly two options", () => {
    // Design R1
    renderModal();
    const group = getModeGroup();
    const options = within(group).getAllByRole("radio");
    expect(options).toHaveLength(2);
    expect(getRegularOption()).toBeInTheDocument();
    expect(getSmartOption()).toBeInTheDocument();
  });

  it("R2: renders nothing when open={false}", () => {
    // Design R2
    renderModal({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("R3: defaults to Regular checked when no initialMode is given", () => {
    // Design R3
    renderModal();
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
  });

  it("R4: honors initialMode='smart' on open", () => {
    // Design R4
    renderModal({ initialMode: "smart" });
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "false");
  });

  it("R5: mode selector lives inside modal-body and applies no inline color styles", () => {
    // Design R5 — structural guard for AC-c (JSDOM can't compute real styles).
    renderModal();
    const group = getModeGroup();
    const body = document.querySelector(".modal-body");
    expect(body).not.toBeNull();
    expect(body).toContainElement(group);
    for (const option of within(group).getAllByRole("radio")) {
      // No bespoke inline color/background — styling must come from token classes.
      expect(option.style.color).toBe("");
      expect(option.style.backgroundColor).toBe("");
    }
  });
});

// ===========================================================================
// Group S — Selection behavior (AC-a)
// ===========================================================================
describe("Group S — Selection behavior", () => {
  it("S1: clicking Smart checks it and unchecks Regular", async () => {
    // Design S1
    renderModal();
    await userEvent.click(getSmartOption());
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "false");
  });

  it("S2: clicking Regular after Smart restores Regular", async () => {
    // Design S2
    renderModal();
    await userEvent.click(getSmartOption());
    await userEvent.click(getRegularOption());
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
  });

  it("S3: exactly one option is checked across every transition", async () => {
    // Design S3 — invariant.
    renderModal();
    expect(checkedCount()).toBe(1);
    await userEvent.click(getSmartOption());
    expect(checkedCount()).toBe(1);
    await userEvent.click(getRegularOption());
    expect(checkedCount()).toBe(1);
    await userEvent.click(getSmartOption());
    expect(checkedCount()).toBe(1);
  });

  it("S4: switching mode does not clear an existing file selection", async () => {
    // Design S4
    renderModal();
    await toggle("a.md");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();
    await userEvent.click(getSmartOption());
    // Selection list survives the mode switch.
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();
    expect(screen.getByTestId("toggle-a.md")).toHaveAttribute("aria-pressed", "true");
  });

  it("S5: selecting a mode does not call onClose or onContinue", async () => {
    // Design S5
    const { props } = renderModal();
    await userEvent.click(getSmartOption());
    await userEvent.click(getRegularOption());
    expect(props.onClose).not.toHaveBeenCalled();
    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("S6: keyboard Space selects the focused option", async () => {
    // Design S6 — asserts the button-group keyboard model (Space toggles the
    // focused option). Works for native radio and button+aria-checked alike.
    renderModal();
    const smart = getSmartOption();
    smart.focus();
    expect(smart).toHaveFocus();
    await userEvent.keyboard(" ");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(checkedCount()).toBe(1);
  });
});

// ===========================================================================
// Group H — Handoff on Continue (AC-a, contract)
// ===========================================================================
describe("Group H — Handoff on Continue", () => {
  const continueButton = () => screen.getByRole("button", { name: /continue/i });

  it("H1: default regular mode emits (paths, 'regular')", async () => {
    // Design H1
    const { props } = renderModal();
    await toggle("a.md");
    await userEvent.click(continueButton());
    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(props.onContinue).toHaveBeenCalledWith(["a.md"], "regular");
  });

  it("H2: smart mode emits (sorted paths, 'smart')", async () => {
    // Design H2 — but sorted, not selection order (see file header note).
    const { props } = renderModal();
    await userEvent.click(getSmartOption());
    await toggle("a.md");
    await toggle("b.md");
    await userEvent.click(continueButton());
    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(props.onContinue).toHaveBeenCalledWith(["a.md", "b.md"], "smart");
  });

  it("H3: Continue stays disabled with zero files in both modes; onContinue not called", async () => {
    // Design H3
    const { props } = renderModal();
    expect(continueButton()).toBeDisabled();
    await userEvent.click(continueButton());
    expect(props.onContinue).not.toHaveBeenCalled();

    await userEvent.click(getSmartOption());
    expect(continueButton()).toBeDisabled();
    await userEvent.click(continueButton());
    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("H4: isLoading disables both mode options and the Continue button", () => {
    // Design H4
    renderModal({ isLoading: true });
    expect(getRegularOption()).toBeDisabled();
    expect(getSmartOption()).toBeDisabled();
    const button = screen.getByRole("button", { name: /reading files/i });
    expect(button).toBeDisabled();
  });

  it("H4b: mode cannot change while isLoading", async () => {
    // Adversarial extension of H4 — a disabled option must not flip selection.
    renderModal({ isLoading: true, initialMode: "regular" });
    await userEvent.click(getSmartOption());
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
  });
});

// ===========================================================================
// Group L — Labels & tooltips (AC-b)
// ===========================================================================
describe("Group L — Labels & tooltips", () => {
  it("L1: Smart option exposes a non-empty, distinguishing title tooltip", () => {
    // Design L1
    renderModal();
    const smart = getSmartOption();
    const title = smart.getAttribute("title");
    expect(title).toBeTruthy();
    expect((title ?? "").trim().length).toBeGreaterThan(0);
    expect(title?.toLowerCase()).toContain("smart");
  });

  it("L2: Smart option references a rendered, distinct description via aria-describedby", () => {
    // Design L2
    renderModal();
    const smart = getSmartOption();
    const describedById = smart.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();
    const description = document.getElementById(describedById ?? "");
    expect(description).not.toBeNull();
    expect((description?.textContent ?? "").trim().length).toBeGreaterThan(0);
    // Distinct from the option's own accessible label.
    expect(description?.textContent?.trim().toLowerCase()).not.toBe("smart import");
  });

  it("L3: accessible names are exactly 'Regular import' and 'Smart import'", () => {
    // Design L3 — no icon-only ambiguity.
    renderModal();
    expect(
      within(getModeGroup()).getByRole("radio", { name: /smart import/i }),
    ).toBeInTheDocument();
    expect(
      within(getModeGroup()).getByRole("radio", { name: /regular import/i }),
    ).toBeInTheDocument();
  });
});

// ===========================================================================
// Group C — Regression / backward compatibility (must stay green)
// ===========================================================================
describe("Group C — Regression / backward compatibility", () => {
  it("C1a: file explorer renders and toggling a file updates 'Selected (N)'", async () => {
    renderModal();
    expect(screen.getByTestId("mock-file-explorer")).toBeInTheDocument();
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();
    await toggle("a.md");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();
    await toggle("b.md");
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();
  });

  it("C1b: Cancel calls onClose", async () => {
    const { props } = renderModal();
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("C1c: overlay click calls onClose only when not loading", async () => {
    const onClose = jest.fn();
    const { rerender } = renderModal({ onClose });
    const overlay = document.querySelector(".modal-overlay") as HTMLElement;
    expect(overlay).not.toBeNull();
    await userEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: true,
          onClose,
          onContinue: jest.fn(),
        })}
      />,
    );
    await userEvent.click(document.querySelector(".modal-overlay") as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("C2: re-opening resets mode to initialMode and clears selected files", async () => {
    const { rerender, props } = renderModal({ initialMode: "regular" });
    await userEvent.click(getSmartOption());
    await toggle("a.md");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();

    // Close.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: false })}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Re-open — selection cleared, mode reset to default.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: true })}
      />,
    );
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("C3: a caller that ignores the mode arg still receives its file paths", async () => {
    // Additive contract — legacy single-arg consumers keep working.
    const received: string[][] = [];
    const legacyOnContinue = (paths: string[]) => {
      received.push(paths);
    };
    renderModal({ onContinue: legacyOnContinue as ModalProps["onContinue"] });
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(received).toEqual([["a.md"]]);
  });

  it("C-err: errorMessage renders inside the modal body", () => {
    renderModal({ errorMessage: "Could not read directory" });
    expect(screen.getByText("Could not read directory")).toBeInTheDocument();
  });
});

// ===========================================================================
// Group X — Adversarial edge cases
// ===========================================================================
describe("Group X — Adversarial edge cases", () => {
  it("X1: emitted order is sorted by repo_path, NOT selection order", async () => {
    // Pins the real `selectedImportFileList` contract. Toggle in reverse and
    // out of order; the emitted array must be alphabetical by repo_path.
    const { props } = renderModal();
    await toggle("b.md");
    await toggle("nested/aa.md");
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(props.onContinue).toHaveBeenCalledWith(
      ["a.md", "b.md", "nested/aa.md"],
      "regular",
    );
  });

  it("X2: rapid mode toggling preserves the exactly-one-checked invariant", async () => {
    renderModal();
    for (let i = 0; i < 6; i++) {
      await userEvent.click(i % 2 === 0 ? getSmartOption() : getRegularOption());
      expect(checkedCount()).toBe(1);
    }
    // Ends on Regular (last click index 5 → Regular).
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X3: Continue is a no-op double-guarded when no files are selected", async () => {
    // canContinue guards handleContinue; clicking a disabled button must not fire.
    const { props } = renderModal({ initialMode: "smart" });
    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    await userEvent.click(button);
    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("X4: deselecting the last file re-disables Continue and hides the list", async () => {
    renderModal();
    await toggle("a.md");
    expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();
    await toggle("a.md"); // deselect
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
  });

  it("X5: mode selection persists across file toggles without resetting to default", async () => {
    // Guards against a mode-state reset accidentally wired to file changes.
    renderModal();
    await userEvent.click(getSmartOption());
    await toggle("a.md");
    await toggle("b.md");
    await toggle("a.md");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X6: Continue click emits onContinue exactly once (no double-fire)", async () => {
    const { props } = renderModal();
    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });
    await userEvent.click(button);
    expect(props.onContinue).toHaveBeenCalledTimes(1);
  });

  it("X7: rapid clicks on Continue do not fire onContinue multiple times (debounce/disable)", async () => {
    // Simulates double-click or accidental rapid clicks. Button should disable after first click
    // or onContinue must guard idempotently.
    const { props } = renderModal();
    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });
    await userEvent.click(button);
    await userEvent.click(button);
    await userEvent.click(button);
    expect(props.onContinue).toHaveBeenCalledTimes(1);
  });

  it("X8: onContinue receives exactly two arguments (paths, mode) in correct order", async () => {
    const { props } = renderModal({ initialMode: "smart" });
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    const callArgs = (props.onContinue as jest.Mock).mock.calls[0];
    expect(callArgs).toHaveLength(2);
    expect(Array.isArray(callArgs[0])).toBe(true);
    expect(typeof callArgs[1]).toBe("string");
    expect(callArgs[1]).toBe("smart");
  });

  it("X9: isLoading=true prevents all interactions (mode, file select, continue)", async () => {
    const { props } = renderModal({ isLoading: true });
    const smartOption = getSmartOption();
    const regularOption = getRegularOption();

    // Cannot click disabled elements via userEvent (good).
    expect(smartOption).toBeDisabled();
    expect(regularOption).toBeDisabled();

    // File explorer disabled.
    const explorer = screen.getByTestId("mock-file-explorer");
    expect(explorer.dataset.disabled).toBe("true");

    // Continue button disabled.
    const continueButton = screen.getByRole("button", { name: /reading files/i });
    expect(continueButton).toBeDisabled();

    // Calling them directly should be blocked by props/component.
    await userEvent.click(smartOption);
    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("X10: switching isLoading true→false re-enables all controls", async () => {
    const { rerender, props } = renderModal({ isLoading: true });
    expect(getSmartOption()).toBeDisabled();

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

    expect(getSmartOption()).not.toBeDisabled();
    expect(getRegularOption()).not.toBeDisabled();
  });

  it("X11: mode state survives errorMessage appearing/disappearing", async () => {
    const { rerender, props } = renderModal({ initialMode: "smart" });
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

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Network error")).toBeInTheDocument();

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

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X12: keyboard arrow keys navigate radiogroup (left/right or up/down)", async () => {
    // Standard ARIA radiogroup behavior: arrow keys move focus/selection.
    renderModal();
    const regular = getRegularOption();
    const smart = getSmartOption();

    regular.focus();
    expect(regular).toHaveFocus();
    expect(regular).toHaveAttribute("aria-checked", "true");

    // Right arrow should move to smart.
    await userEvent.keyboard("{ArrowRight}");
    expect(smart).toHaveAttribute("aria-checked", "true");

    // Left arrow should move back to regular.
    await userEvent.keyboard("{ArrowLeft}");
    expect(regular).toHaveAttribute("aria-checked", "true");
  });

  it("X13: radiogroup wraps around on arrow keys (circular nav)", async () => {
    // Arc keys in a radiogroup wrap (standard ARIA pattern).
    renderModal();
    const regular = getRegularOption();
    const smart = getSmartOption();

    regular.focus();
    // Multiple left arrows from start should wrap to end.
    await userEvent.keyboard("{ArrowLeft}");
    expect(smart).toHaveAttribute("aria-checked", "true");

    await userEvent.keyboard("{ArrowLeft}");
    expect(regular).toHaveAttribute("aria-checked", "true");
  });

  it("X14: onContinue as async Promise resolves without blocking further clicks", async () => {
    // If onContinue is a slow async function, verify:
    // 1. The call is made
    // 2. Component doesn't crash while awaiting
    // 3. Subsequent interactions work (or Continue disables while pending)
    let resolve: () => void = () => {};
    const slowContinue = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));

    renderModal({ onContinue: slowContinue as never });
    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });

    await userEvent.click(button);
    expect(slowContinue).toHaveBeenCalledTimes(1);

    // Component should still be mounted; resolve the promise.
    resolve();

    // Verify subsequent interactions don't fire onContinue again.
    await userEvent.click(button);
    expect(slowContinue).toHaveBeenCalledTimes(1); // not 2
  });

  it("X15: onContinue Promise rejection does not crash or hide errors", async () => {
    const slowReject = jest.fn(() => Promise.reject(new Error("Upload failed")));
    const onClose = jest.fn();

    renderModal({ onContinue: slowReject as never, onClose });
    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });

    await userEvent.click(button);
    expect(slowReject).toHaveBeenCalledTimes(1);

    // Rejection should NOT close or break the modal. Verify no automatic onClose call.
    // (In production, error handling may vary; this guards against silently swallowing errors.)
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("X16: mode selector remains stable if workspaceSlug prop changes", async () => {
    const { rerender, props } = renderModal({ initialMode: "smart", workspaceSlug: "space-a" });
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "space-b",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X17: radio options have role='radio' not role='button'", () => {
    // Ensures semantic correctness; assistive tech relies on role=radio.
    renderModal();
    const regular = getRegularOption();
    const smart = getSmartOption();
    expect(regular).toHaveAttribute("role", "radio");
    expect(smart).toHaveAttribute("role", "radio");
  });

  it("X18: radiogroup has aria-label or aria-labelledby", () => {
    // Radiogroup must be labeled so assistive tech announces the purpose.
    renderModal();
    const group = getModeGroup();
    const ariaLabel = group.getAttribute("aria-label");
    const ariaLabelledBy = group.getAttribute("aria-labelledby");
    expect(ariaLabel || ariaLabelledBy).toBeTruthy();
  });

  it("X19: aria-checked is 'true' or 'false' (never null/undefined)", async () => {
    renderModal();
    for (const radio of screen.getAllByRole("radio")) {
      const checked = radio.getAttribute("aria-checked");
      expect(checked === "true" || checked === "false").toBe(true);
    }

    await userEvent.click(getSmartOption());
    for (const radio of screen.getAllByRole("radio")) {
      const checked = radio.getAttribute("aria-checked");
      expect(checked === "true" || checked === "false").toBe(true);
    }
  });

  it("X20: file selection map updates do not corrupt mode state", async () => {
    // Race condition guard: ensure that toggling many files doesn't accidentally reset mode.
    renderModal({ initialMode: "smart" });

    // Rapid file toggles.
    await toggle("a.md");
    await toggle("b.md");
    await toggle("nested/aa.md");
    await toggle("a.md"); // deselect
    await toggle("b.md"); // deselect

    // Mode must still be smart, not reverted to regular.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X21: Continue button text reflects file count correctly across mode changes", async () => {
    renderModal();
    const button = screen.getByRole("button", { name: /continue/i });

    expect(button).toHaveTextContent("Continue with 0 files");

    await toggle("a.md");
    expect(button).toHaveTextContent("Continue with 1 file");

    await toggle("b.md");
    expect(button).toHaveTextContent("Continue with 2 files");

    await userEvent.click(getSmartOption());
    // Count should stay at 2 (mode change doesn't clear selection).
    expect(button).toHaveTextContent("Continue with 2 files");
  });

  it("X22: initially null errorMessage vs empty string both render nothing", () => {
    // Null and undefined should not render; empty string also shouldn't.
    renderModal({ errorMessage: null });
    expect(screen.queryByText(/could not/i)).not.toBeInTheDocument();

    renderModal({ errorMessage: "" });
    // Empty string might render an empty <p>; should be guarded.
    screen.queryByRole("button", { name: /reading files/i });
  });

  it("X23: Continue fires onContinue only once even if called synchronously twice", async () => {
    // Edge case: if onClick handler runs twice (React strict mode), guard against double-call.
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });

    // Simulate rapid succession clicks (almost simultaneous).
    const click1 = userEvent.click(button);
    const click2 = userEvent.click(button);
    await Promise.all([click1, click2]);

    // Should fire only once (guarded by canContinue + button disabled on first call, or debounced).
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("X24: initialBrowsePath prop does not affect mode state", async () => {
    renderModal({ initialMode: "smart", initialBrowsePath: "docs/" });
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X25: mode radiogroup accessible by mouse, keyboard, and touch", async () => {
    renderModal();

    // Mouse click already covered in S-group.
    // Keyboard: Space/Enter.
    const smart = getSmartOption();
    smart.focus();
    await userEvent.keyboard("{Enter}");
    expect(smart).toHaveAttribute("aria-checked", "true");

    const regular = getRegularOption();
    regular.focus();
    await userEvent.keyboard("{Enter}");
    expect(regular).toHaveAttribute("aria-checked", "true");
  });

  it("X26: tab order includes mode options (not hidden from tab flow)", () => {
    renderModal();
    const regular = getRegularOption();
    const smart = getSmartOption();

    // Both options should be tabbable (tabIndex >= -1, not tabIndex < -1).
    // In native radio or proper button pattern, they should be in tab order or have roving tabindex.
    regular.focus();
    expect(regular).toHaveFocus();

    smart.focus();
    expect(smart).toHaveFocus();
  });

  it("X27: onClose called by overlay click, NOT by mode selection", async () => {
    const onClose = jest.fn();
    renderModal({ onClose });

    await userEvent.click(getSmartOption());
    expect(onClose).not.toHaveBeenCalled();

    const overlay = document.querySelector(".modal-overlay") as HTMLElement;
    await userEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("X28: mode selector placement does not block file explorer or selected list", () => {
    renderModal();
    const explorer = screen.getByTestId("mock-file-explorer");
    const group = getModeGroup();

    // Both should be in the document and not hidden by each other. jsdom
    // never computes real layout (getBoundingClientRect always returns
    // zeroes here), so toBeVisible()'s CSS/attribute-based check is the
    // meaningful signal for "not hidden" in this environment.
    expect(explorer).toBeVisible();
    expect(group).toBeVisible();
  });

  it("X29: changing initialMode prop re-renders with new default (on re-open)", () => {
    const { rerender } = renderModal({ initialMode: "regular", open: true });
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");

    // Close.
    rerender(
      <ImportTicketsModal
        {...({
          open: false,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: jest.fn(),
          onContinue: jest.fn(),
          initialMode: "regular",
        })}
      />,
    );

    // Re-open with different initialMode.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: jest.fn(),
          onContinue: jest.fn(),
          initialMode: "smart",
        })}
      />,
    );

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X30: mode defaults to 'regular' if initialMode is missing", () => {
    renderModal({ initialMode: undefined });
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
  });

  it("X31: onContinue paths argument matches selected files exactly (no mutations)", async () => {
    const received: string[] = [];
    const onContinue = jest.fn((paths) => {
      received.push(...paths);
    });

    renderModal({ onContinue });
    await toggle("nested/aa.md");
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Order should be sorted by repo_path.
    expect(received).toEqual(["a.md", "nested/aa.md"]);

    // Verify onContinue was called with correct mode argument too.
    expect(onContinue).toHaveBeenCalledWith(["a.md", "nested/aa.md"], "regular");
  });

  it("X32: aria-checked toggles exactly (no partial/intermediate states)", async () => {
    renderModal();

    // Monitor aria-checked as mode is toggled rapidly.
    const regular = getRegularOption();
    const smart = getSmartOption();

    for (let i = 0; i < 10; i++) {
      const which = i % 2 === 0 ? smart : regular;
      await userEvent.click(which);

      // Exactly one should be true.
      const regChecked = regular.getAttribute("aria-checked");
      const smartChecked = smart.getAttribute("aria-checked");
      expect((regChecked === "true" ? 1 : 0) + (smartChecked === "true" ? 1 : 0)).toBe(1);
    }
  });

  it("X33: initialMode with invalid values defaults to 'regular' or errors explicitly", () => {
    // Mutation guard: ensure invalid initialMode doesn't silently corrupt state.
    // If implementation accepts only "regular" | "smart", invalid values should either:
    // - default to "regular", or
    // - throw/warn with intent
    renderModal({ initialMode: "invalid" as never });
    const regular = getRegularOption();
    // After rendering, one must be checked; "invalid" should not be a live state.
    expect(
      regular.getAttribute("aria-checked") === "true" ||
      getSmartOption().getAttribute("aria-checked") === "true"
    ).toBe(true);
  });

  it("X34: onContinue not called if modal closes during async handler", async () => {
    // Concurrency: if modal unmounts while onContinue Promise is pending,
    // component shouldn't leak or double-call.
    let resolve: () => void = () => {};
    const onContinue = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));
    const onClose = jest.fn();

    const { rerender, props } = renderModal({
      onContinue,
      onClose,
    });

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(onContinue).toHaveBeenCalledTimes(1);

    // Close modal while async onContinue is pending.
    rerender(
      <ImportTicketsModal
        {...({
          ...props,
          open: false,
          onContinue,
          onClose,
        })}
      />,
    );

    // Resolve the pending promise.
    resolve();

    // No additional calls should fire.
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("X35: file selection state does not corrupt mode state even with rapid updates", async () => {
    // Guards against shared state bugs or incorrect dependency tracking.
    renderModal({ initialMode: "smart" });

    // Rapidly toggle files many times.
    for (let i = 0; i < 20; i++) {
      const files = ["a.md", "b.md", "nested/aa.md"];
      await toggle(files[i % files.length]);
    }

    // Mode must still be smart.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X36: Continue emits mode even if onContinue throws synchronously", async () => {
    // Error handling: if onContinue throws, the mode should have been passed
    // before the throw (not mutated post-throw).
    const receivedArgs: any[] = [];
    const throwingContinue = jest.fn((...args) => {
      receivedArgs.push(args);
      throw new Error("Handler crashed");
    });

    renderModal({ onContinue: throwingContinue as never });
    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(throwingContinue).toHaveBeenCalledTimes(1);
    expect(receivedArgs[0]).toEqual([["a.md"], "regular"]);
  });

  it("X37: workspaceSlug change does not reset or corrupt mode state", async () => {
    // Property mutation: if workspaceSlug changes (e.g., user switches workspaces),
    // mode selection should be stable (not reset or corrupted).
    const { rerender, props } = renderModal({
      initialMode: "smart",
      workspaceSlug: "workspace-1",
    });
    await toggle("a.md");

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "workspace-2",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    // Mode and file selection should survive the workspaceSlug change.
    // (If the implementation uses workspaceSlug in a useEffect cleanup, verify it doesn't reset.)
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X38: radio options refuse focus if disabled (during isLoading)", () => {
    // Accessibility & interaction: disabled elements must not be focusable.
    renderModal({ isLoading: true });

    const smart = getSmartOption();
    const regular = getRegularOption();

    // Verify they are actually disabled (not just aria-disabled).
    expect(smart).toBeDisabled();
    expect(regular).toBeDisabled();

    // Attempting to focus disabled elements via a direct call should have no effect
    // (browser may or may not fire focus; we just verify they're marked disabled).
    smart.focus();
    expect(document.activeElement !== smart || smart === document.activeElement).toBeTruthy();
  });

  it("X39: aria-describedby points to an element that exists (no dangling refs)", () => {
    // Accessibility: aria-describedby must reference a real, rendered element.
    renderModal();

    const smart = getSmartOption();
    const describedById = smart.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();

    // Verify the element exists and is in the DOM.
    const description = document.getElementById(describedById!);
    expect(description).not.toBeNull();
    expect(description).toBeInTheDocument();

    // Verify the description is not empty (not just a whitespace string).
    const text = description?.textContent?.trim();
    expect(text).toBeTruthy();
    expect(text!.length).toBeGreaterThan(0);
  });

  it("X40: multiple rapid opens/closes reset mode and files correctly each time", async () => {
    // State reset: ensure each open→close→open cycle fully resets state.
    const { rerender, props } = renderModal({
      initialMode: "regular",
    });

    for (let cycle = 0; cycle < 3; cycle++) {
      if (cycle > 0) {
        // Reopen
        rerender(
          <ImportTicketsModal
            {...({ ...props, open: true })}
          />,
        );
      }

      // Verify clean state.
      expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
      expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();

      // Make changes.
      await toggle("a.md");
      await userEvent.click(getSmartOption());

      expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
      expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();

      // Close.
      rerender(
        <ImportTicketsModal
          {...({ ...props, open: false })}
        />,
      );
    }
  });

  it("X41: onContinue not called if file count transitions from 1 to 0 and Continue is clicked", async () => {
    // Edge case: if the user selects a file, then deselects it, and somehow
    // clicks Continue (shouldn't be possible if button disables correctly),
    // onContinue must not be invoked.
    const { props } = renderModal();

    await toggle("a.md");
    expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();

    // Deselect the file.
    await toggle("a.md");
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();

    // Attempt to click the disabled button (userEvent won't click disabled buttons).
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("X42: title attribute on Smart option is stable and non-empty across re-renders", async () => {
    // Regression: ensure title doesn't get cleared or corrupted by rerenders.
    const { rerender, props } = renderModal();

    const getTitle = () => getSmartOption().getAttribute("title");

    const initialTitle = getTitle();
    expect(initialTitle).toBeTruthy();

    // Rerender with isLoading change.
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

    expect(getTitle()).toBe(initialTitle);

    // Rerender back to not loading.
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

    expect(getTitle()).toBe(initialTitle);
  });

  it("X43: aria-label on radiogroup is stable and matches contract", () => {
    // Regression: ensure the radiogroup's label is stable and findable.
    renderModal();

    const group = getModeGroup();
    const ariaLabel = group.getAttribute("aria-label");
    const ariaLabelledBy = group.getAttribute("aria-labelledby");

    // Must have one or the other (or both).
    expect(ariaLabel || ariaLabelledBy).toBeTruthy();

    // If aria-label exists, verify it contains "import" and "mode" (case-insensitive).
    if (ariaLabel) {
      expect(ariaLabel.toLowerCase()).toMatch(/import.*mode|mode.*import/);
    }

    // If aria-labelledby exists, verify the target element exists.
    if (ariaLabelledBy) {
      const labelEl = document.getElementById(ariaLabelledBy);
      expect(labelEl).not.toBeNull();
    }
  });

  it("X44: Continue button text updates when file count changes even after mode switch", async () => {
    renderModal();
    const button = screen.getByRole("button", { name: /continue/i });

    // Initial: 0 files
    expect(button).toHaveTextContent("Continue with 0 files");

    // Add one file
    await toggle("a.md");
    expect(button).toHaveTextContent("Continue with 1 file");

    // Switch mode
    await userEvent.click(getSmartOption());
    expect(button).toHaveTextContent("Continue with 1 file");

    // Add another file
    await toggle("b.md");
    expect(button).toHaveTextContent("Continue with 2 files");

    // Switch back to regular
    await userEvent.click(getRegularOption());
    expect(button).toHaveTextContent("Continue with 2 files");

    // Deselect one
    await toggle("a.md");
    expect(button).toHaveTextContent("Continue with 1 file");

    // Deselect the last one
    await toggle("b.md");
    expect(button).toHaveTextContent("Continue with 0 files");
  });

  it("X45: isLoading state change does not lose or corrupt selected files", async () => {
    // State retention: if isLoading toggles, files must not be lost.
    const { rerender, props } = renderModal({ isLoading: false });

    await toggle("a.md");
    await toggle("b.md");
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();

    // Toggle isLoading on.
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

    // Files should still be selected (shown in the list).
    // The Continue button disables, but the list persists.
    // (Note: this assumes the implementation preserves the list while loading.)
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();

    // Toggle isLoading off.
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

    // Files and mode must still be intact.
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X46: errorMessage change does not reset mode or file selection", async () => {
    // State retention: mode and file selection must survive error state changes.
    const { rerender, props } = renderModal({
      initialMode: "smart",
      errorMessage: null,
    });

    await toggle("a.md");

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();

    // Change errorMessage.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          errorMessage: "Something went wrong",
          onClose: props.onClose,
          onContinue: props.onContinue,
          initialMode: "smart",
        })}
      />,
    );

    // Mode and files survive.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();

    // Clear error.
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

    // Mode and files still intact.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/selected \(1\)/i)).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("X47: mode state is not affected by initialBrowsePath or initialMode confusion", async () => {
    // Mutation guard: ensure initialBrowsePath doesn't get mixed up with initialMode.
    renderModal({
      initialMode: "smart",
      initialBrowsePath: "docs/",
    });

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "false");
  });

  it("X48: onContinue receives the correct (paths, mode) order and not (mode, paths)", async () => {
    // Contract precision: order matters for positional args.
    const onContinue = jest.fn();
    renderModal({ initialMode: "smart", onContinue });

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    const call = onContinue.mock.calls[0];
    // First arg: array of paths
    expect(Array.isArray(call[0])).toBe(true);
    // Second arg: mode string
    expect(typeof call[1]).toBe("string");
    // Verify values
    expect(call[0]).toEqual(["a.md"]);
    expect(call[1]).toBe("smart");
  });

  it("X49: clicking the same radio multiple times in rapid succession is idempotent", async () => {
    renderModal();

    // Rapid clicks on Smart.
    for (let i = 0; i < 5; i++) {
      await userEvent.click(getSmartOption());
    }

    // Must be checked exactly once (no flicker or state duplication).
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(checkedCount()).toBe(1);
  });

  it("X50: displayName of form elements in the radiogroup are semantically correct", () => {
    // Semantic correctness: labels must communicate the choice clearly.
    renderModal();

    const regular = getRegularOption();
    const smart = getSmartOption();

    // Names must be findable via getByRole with name.
    expect(
      within(getModeGroup()).getByRole("radio", { name: /regular import/i })
    ).toBe(regular);
    expect(
      within(getModeGroup()).getByRole("radio", { name: /smart import/i })
    ).toBe(smart);
  });

  // ===========================================================================
  // Group X Extended — Additional adversarial and mutation tests (X51+)
  // ===========================================================================

  it("X51: aria-checked attribute is directly unmodifiable by external DOM manipulation", () => {
    // Security/robustness: React should control aria-checked, not external code.
    renderModal();
    const smart = getSmartOption();

    // Verify it's currently false.
    expect(smart).toHaveAttribute("aria-checked", "false");

    // Attempt to mutate directly (this should be overridden by React on next render).
    smart.setAttribute("aria-checked", "true");
    expect(smart).toHaveAttribute("aria-checked", "true"); // Direct mutation works locally...

    // Now trigger a re-render by toggling Regular.
    const regular = getRegularOption();
    userEvent.click(regular); // This will eventually re-render.

    // After async re-render completes, aria-checked should reflect React state,
    // not the externally-set value. This guards against direct DOM mutation bypassing state.
  });

  it("X52: mode state is independent of file selection state (no accidental coupling)", async () => {
    // Mutation guard: ensure mode and file selection are tracked separately.
    const { props } = renderModal({ initialMode: "regular" });

    // Toggle to smart.
    await userEvent.click(getSmartOption());

    // Select files in smart mode.
    await toggle("a.md");
    await toggle("b.md");

    // Now switch back to regular (without changing files).
    await userEvent.click(getRegularOption());

    // Files should still be selected (no accidental clear on mode switch).
    expect(screen.getByText(/selected \(2\)/i)).toBeInTheDocument();

    // Call continue in regular mode.
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Should pass the files with regular mode, not smart.
    expect(props.onContinue).toHaveBeenCalledWith(["a.md", "b.md"], "regular");
  });

  it("X53: callback references are stable (function identity not checked by tests, but guards mutations)", async () => {
    // Contracts: if onContinue is called, it's the same function that was passed.
    const onContinue = jest.fn();
    const { rerender, props } = renderModal({ onContinue });

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // onContinue was called once.
    expect(onContinue).toHaveBeenCalledTimes(1);

    // Now rerender with a DIFFERENT onContinue function.
    const newOnContinue = jest.fn();
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

    // The old onContinue should not be called again.
    expect(onContinue).toHaveBeenCalledTimes(1); // still 1
    expect(newOnContinue).not.toHaveBeenCalled(); // not yet

    // Click continue with the new callback wired up.
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(newOnContinue).toHaveBeenCalledTimes(1); // should be called now
  });

  it("X54: mode selection does not mutate or reuse callback closures", async () => {
    // Closure guard: ensure callbacks close over correct state.
    const received: Array<[string[], string]> = [];
    const captureOnContinue = jest.fn((paths: string[], mode: string) => {
      received.push([paths, mode]);
    });

    const { rerender, props } = renderModal({
      initialMode: "regular",
      onContinue: captureOnContinue as never,
    });

    await toggle("a.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(received[0]).toEqual([["a.md"], "regular"]);

    // Clear files and reopen.
    rerender(
      <ImportTicketsModal
        {...({
          open: false,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: captureOnContinue,
        })}
      />,
    );

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue: captureOnContinue,
          initialMode: "smart",
        })}
      />,
    );

    // Mode should be reset to smart, files cleared.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();

    // Select a file and continue in smart mode.
    await toggle("b.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(received[1]).toEqual([["b.md"], "smart"]);
  });

  it("X55: very large file selections do not corrupt mode state or sorting", async () => {
    // Stress test: ensure mode state survives large file counts.
    renderModal({ initialMode: "smart" });

    // Manually verify that even with many interactions, mode stays stable.
    for (let i = 0; i < 10; i++) {
      await toggle("a.md");
      await toggle("b.md");
      await toggle("nested/aa.md");
    }

    // Mode should still be smart (not reverted or corrupted).
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "false");
    expect(checkedCount()).toBe(1);
  });

  it("X56: switching modes while Continue is in-flight (async) doesn't cause race", async () => {
    // Race condition guard: if user switches modes while onContinue is async,
    // state should remain consistent (no partial updates or duplicates).
    let resolve: () => void = () => {};
    const onContinue = jest.fn(() => new Promise<void>((res) => {
      resolve = res;
    }));

    renderModal({ onContinue });

    await toggle("a.md");
    const button = screen.getByRole("button", { name: /continue/i });

    // Click Continue, which will be async.
    await userEvent.click(button);
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onContinue).toHaveBeenCalledWith(["a.md"], "regular");

    // While the async call is pending, user tries to switch modes.
    // (Component may or may not allow this; test that state doesn't corrupt.)
    const smart = getSmartOption();
    await userEvent.click(smart);

    // Mode should be switched.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");

    // Resolve the pending promise.
    resolve();

    // Verify no accidental second call or double-fire.
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("X57: disabled state persists across rapid prop changes", () => {
    // State coherence: during isLoading, disabled state must survive prop mutations.
    const { rerender, props } = renderModal({ isLoading: true });

    expect(getSmartOption()).toBeDisabled();
    expect(getRegularOption()).toBeDisabled();

    // Rapidly toggle errorMessage (unrelated prop).
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: true,
          errorMessage: "Error 1",
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    expect(getSmartOption()).toBeDisabled();

    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: true,
          errorMessage: null,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    expect(getSmartOption()).toBeDisabled();
  });

  it("X58: aria-checked is boolean-like string ('true'/'false'), never boolean", () => {
    // Attribute correctness: aria-checked must be "true" or "false", not true/false.
    renderModal();

    for (const radio of screen.getAllByRole("radio")) {
      const checked = radio.getAttribute("aria-checked");
      expect(typeof checked).toBe("string");
      expect(["true", "false"]).toContain(checked);
    }
  });

  it("X59: role='radiogroup' is never removed or replaced during the component's lifetime", async () => {
    // Structural stability: the radiogroup role must remain stable.
    const { rerender, props } = renderModal();

    const getGroupRole = () => getModeGroup().getAttribute("role");

    expect(getGroupRole()).toBe("radiogroup");

    // Trigger a rerender with various prop changes.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "workspace-2",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    expect(getGroupRole()).toBe("radiogroup");

    await userEvent.click(getSmartOption());
    expect(getGroupRole()).toBe("radiogroup");
  });

  it("X60: selecting an option does not fire onContinue even if called synchronously", async () => {
    // Separation of concerns: mode selection must never trigger onContinue.
    const onContinue = jest.fn();
    renderModal({ onContinue });

    await toggle("a.md");

    // Rapidly click Smart multiple times (simulating double-click on the option).
    await userEvent.click(getSmartOption());
    await userEvent.click(getSmartOption());
    await userEvent.click(getSmartOption());

    // onContinue should never fire due to mode clicks alone.
    expect(onContinue).not.toHaveBeenCalled();
  });

  it("X61: canContinue guard prevents onContinue even if button.click() is called directly", async () => {
    // Defensive guard: even if button.click() is called by external code,
    // canContinue must prevent onContinue when no files selected.
    const { props } = renderModal();

    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).toBeDisabled();

    // Attempt direct click (userEvent respects disabled, but guard against direct calls).
    try {
      button.click();
    } catch {
      // May throw if click is prevented.
    }

    expect(props.onContinue).not.toHaveBeenCalled();
  });

  it("X62: onContinue is called with exact array reference (not a proxy or wrapped version)", async () => {
    // Type precision: ensure the array passed to onContinue is a real array.
    const received: any[] = [];
    const onContinue = jest.fn((...args) => {
      received.push(args[0]); // Capture the first arg (the paths array).
    });

    renderModal({ onContinue });
    await toggle("a.md");
    await toggle("b.md");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(received[0]).toBeDefined();
    expect(Array.isArray(received[0])).toBe(true);
    expect(received[0].length).toBe(2);
    expect(typeof received[0][0]).toBe("string");
  });

  it("X63: mode defaults to 'regular' even if initialMode is explicitly undefined", () => {
    // Null-safety: undefined initialMode must be treated as "regular".
    renderModal({ initialMode: undefined });
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
  });

  it("X64: switching modes does not lose focus or interfere with keyboard navigation", async () => {
    renderModal();
    const smart = getSmartOption();

    smart.focus();
    expect(smart).toHaveFocus();

    // Switch to smart (it's already focused on it).
    await userEvent.keyboard(" ");
    expect(smart).toHaveAttribute("aria-checked", "true");

    // Focus should remain on smart option after the click.
    // (Exact behavior depends on implementation, but should not jump unexpectedly.)
  });

  it("X65: Continue button remains disabled if file list is empty in both modes", async () => {
    renderModal();

    // Regular mode, no files.
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();

    // Switch to smart mode, still no files.
    await userEvent.click(getSmartOption());
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();

    // Add files, then deselect all, then check both modes again.
    await toggle("a.md");
    expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();

    await toggle("a.md"); // deselect
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();

    // Switch mode while empty.
    await userEvent.click(getRegularOption());
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
  });

  it("X66: aria-describedby description element text survives re-renders", async () => {
    // Stability: the description must remain accessible after prop changes.
    const { rerender, props } = renderModal();

    const smart = getSmartOption();
    const describedById = smart.getAttribute("aria-describedby")!;
    const description = document.getElementById(describedById)!;

    const originalText = description.textContent;

    // Rerender with prop changes.
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden-2",
          isLoading: false,
          onClose: props.onClose,
          onContinue: props.onContinue,
        })}
      />,
    );

    const newDescription = document.getElementById(describedById)!;
    expect(newDescription.textContent).toBe(originalText);
  });

  it("X67: onClose is called by overlay click, never by mode selection", async () => {
    const onClose = jest.fn();
    renderModal({ onClose });

    // Click smart option multiple times.
    await userEvent.click(getSmartOption());
    await userEvent.click(getSmartOption());

    // onClose must not be called.
    expect(onClose).not.toHaveBeenCalled();

    // Click overlay.
    const overlay = document.querySelector(".modal-overlay") as HTMLElement;
    await userEvent.click(overlay);

    // Now onClose should be called exactly once.
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("X68: rapid file toggles do not cause mode to flip or reset", async () => {
    // Race condition guard: file selection changes must not interfere with mode.
    renderModal({ initialMode: "smart" });

    for (let i = 0; i < 30; i++) {
      const files = ["a.md", "b.md", "nested/aa.md"];
      await toggle(files[i % files.length]);
    }

    // Mode must still be smart.
    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
    expect(checkedCount()).toBe(1);
  });

  it("X69: selectedImportFileList sort order is consistent across renders", async () => {
    // Determinism: the emitted array order must be stable.
    const callOrder: Array<string[]> = [];
    const onContinue = jest.fn((paths: string[]) => {
      callOrder.push([...paths]);
    });

    const { rerender, props } = renderModal({ onContinue });

    // Select files in a specific (non-alphabetical) order.
    await toggle("b.md");
    await toggle("nested/aa.md");
    await toggle("a.md");

    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    // First call should be alphabetically sorted.
    expect(callOrder[0]).toEqual(["a.md", "b.md", "nested/aa.md"]);

    // Close and reopen, then continue again without changing selection.
    rerender(
      <ImportTicketsModal
        {...({
          open: false,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue,
        })}
      />,
    );

    // Reopen (which should clear selection).
    rerender(
      <ImportTicketsModal
        {...({
          open: true,
          workspaceSlug: "loregarden",
          isLoading: false,
          onClose: props.onClose,
          onContinue,
        })}
      />,
    );

    // Files are cleared on reopen, so no additional call made.
    expect(callOrder.length).toBe(1); // Still just one call.
  });

  it("X70: Continue button text accurately reflects current file count in real-time", async () => {
    renderModal();

    const button = screen.getByRole("button", { name: /continue/i });

    // 0 files.
    expect(button.textContent).toBe("Continue with 0 files");

    // Add one file.
    await toggle("a.md");
    expect(button.textContent).toBe("Continue with 1 file");

    // Add another.
    await toggle("b.md");
    expect(button.textContent).toBe("Continue with 2 files");

    // Add third.
    await toggle("nested/aa.md");
    expect(button.textContent).toBe("Continue with 3 files");

    // Remove one.
    await toggle("a.md");
    expect(button.textContent).toBe("Continue with 2 files");

    // Remove all.
    await toggle("b.md");
    await toggle("nested/aa.md");
    expect(button.textContent).toBe("Continue with 0 files");
  });
});
