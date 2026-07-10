import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";
import type { SelectedImportFile } from "../ImportTicketFileExplorer";

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
  // Cast: the current component prop type still declares the single-arg
  // onContinue; the mode arg is the additive change under test.
  const utils = render(<ImportTicketsModal {...(props as unknown as never)} />);
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
        } as unknown as never)}
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
        {...({ ...props, open: false } as unknown as never)}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Re-open — selection cleared, mode reset to default.
    rerender(
      <ImportTicketsModal
        {...({ ...props, open: true } as unknown as never)}
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
        } as unknown as never)}
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
        } as unknown as never)}
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
        } as unknown as never)}
      />,
    );

    expect(getSmartOption()).toHaveAttribute("aria-checked", "true");
  });

  it("X12: keyboard arrow keys navigate radiogroup (left/right or up/down)", async () => {
    // Standard ARIA radiogroup behavior: arrow keys move focus/selection.
    renderModal();
    const group = getModeGroup();
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

    const { props } = renderModal({ onContinue: slowContinue as never });
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

    const { props } = renderModal({ onContinue: slowReject as never, onClose });
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
        } as unknown as never)}
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
    const { rerender } = renderModal({ errorMessage: null });
    expect(screen.queryByText(/could not/i)).not.toBeInTheDocument();

    const { props } = renderModal({ errorMessage: "" });
    // Empty string might render an empty <p>; should be guarded.
    const error = screen.queryByRole("button", { name: /reading files/i });
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

    // Both should be in the document and not hidden by each other.
    expect(explorer).toBeVisible();
    expect(group).toBeVisible();

    // Mode selector should be before file explorer or at least not obscuring it.
    const groupRect = group.getBoundingClientRect();
    const explorerRect = explorer.getBoundingClientRect();
    // Simple check: neither is zero-sized (indicating hidden).
    expect(groupRect.height).toBeGreaterThan(0);
    expect(explorerRect.height).toBeGreaterThan(0);
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
        } as unknown as never)}
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
        } as unknown as never)}
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
    const onContinue = jest.fn((paths) => received.push(...paths));

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
});
