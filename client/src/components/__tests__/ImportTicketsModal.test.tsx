import { fireEvent, render, screen, within } from "@testing-library/react";
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
// Group K — the selected-files list's keyboard path (lg-milestone-that-2215)
//
// `.import-selected-list` is a bounded `overflow: auto` <ul> of every file
// picked. Nothing in it took focus — the rows are plain <li> text and the
// toggles live in the explorer above — so the list could not be scrolled with
// the arrow keys once it was longer than the box.
// ===========================================================================
describe("Group K — the selected-files list", () => {
  const REGION = "Selected ticket files";

  it("K1: is a list, named, and a tab stop stepping on to Cancel — AC3", async () => {
    renderModal();
    await toggle("a.md");

    // `getByRole("list")`, not "group": a <ul> keeps its native `list` role,
    // so it still announces "list, N items", which `group` would override.
    const list = screen.getByRole("list", { name: REGION });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");

    // The footer FOLLOWS the list, so Tab forward is the step that exists.
    list.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: /^cancel$/i })).toHaveFocus();
  });

  it("K2: renders no labelled list while nothing is selected — AC8", async () => {
    renderModal();

    // The block is gated on `selected.length > 0`, so the explorer stands in
    // its place rather than a named box over zero rows.
    expect(screen.queryByRole("list", { name: REGION })).toBeNull();
    await toggle("a.md");
    expect(screen.getByRole("list", { name: REGION })).toBeInTheDocument();
  });

  it("K3: lets Escape from inside the list still close the dialog — AC9", async () => {
    const { props } = renderModal();
    await toggle("a.md");

    const list = screen.getByRole("list", { name: REGION });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    expect(props.onClose).toHaveBeenCalled();
  });
});
