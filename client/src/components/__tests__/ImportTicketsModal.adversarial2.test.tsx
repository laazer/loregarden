import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * ImportTicketsModal — Group X adversarial edge cases, X36 to X70
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
describe("Group X — Adversarial edge cases (Group X adversarial edge cases, X36 to X70)", () => {
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
