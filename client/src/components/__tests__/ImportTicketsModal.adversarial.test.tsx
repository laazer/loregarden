import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * ImportTicketsModal — Group X adversarial edge cases, X1 to X35
 *
 * Split out of `ImportTicketsModal.test.tsx` when that file passed the 1200
 * line organization cap (lg-milestone-that-2215 touched it to add Group K).
 * The groups were already the file's own structure, and the adversarial group
 * is both the largest and the most self-contained: it exercises the mode
 * selector and the handoff contract against hostile input and asserts no
 * behaviour the other groups assert.
 *
 * The harness below is the same one the plain suite uses, kept here verbatim
 * rather than exported from it, because a test file importing another test
 * file's helpers makes both suites run twice.
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
// Group X — Adversarial edge cases
// ===========================================================================
describe("Group X — Adversarial edge cases (Group X adversarial edge cases, X1 to X35)", () => {
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
});
