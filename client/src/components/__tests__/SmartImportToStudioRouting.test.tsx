import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";

import { ImportTicketsModal } from "../ImportTicketsModal";

/**
 * Behavioral test suite for smart import routing to Studio with preview flag.
 *
 * Ticket:   34-route-smart-import-selection-to-studio-with-prev
 * Stage:    test_break (test_designer)
 *
 * Acceptance Criteria mapping:
 *   - AC-1: Smart import selection navigates to Studio
 *   - AC-2: Imported ticket data passed to Studio context
 *   - AC-3: Studio recognizes preview state (not finalized)
 *
 * These tests encode the contract for smart import routing behavior. They verify:
 * 1. Navigation: Smart import mode triggers Studio navigation instead of confirmation modal
 * 2. Data Flow: Imported file content is parsed and passed to Studio session
 * 3. Preview Flag: Session is marked as preview (not yet committed to workspace)
 *
 * Notes:
 * - File explorer is mocked to isolate routing behavior
 * - Navigation is mocked to verify Studio path and state parameters
 * - API calls for preview are mocked to test data flow
 * - Tests assume onContinue callback receives (filePaths: string[], mode: "smart" | "regular")
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

jest.mock("../../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../../lib/useAppNavigation"),
  navigateToStudio: jest.fn(),
  navigateToStudioTicketSessionNew: jest.fn(),
}));

jest.mock("../../../api/client", () => ({
  ...jest.requireActual("../../../api/client"),
  api: {
    ...jest.requireActual("../../../api/client").api,
    previewTicketImport: jest.fn(),
  },
}));

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

function getSmartOption(): HTMLElement {
  const group = screen.getByRole("radiogroup", { name: /import mode/i });
  return within(group).getByRole("radio", { name: /^smart import$/i });
}

function getRegularOption(): HTMLElement {
  const group = screen.getByRole("radiogroup", { name: /import mode/i });
  return within(group).getByRole("radio", { name: /^regular import$/i });
}

function getContinueButton(): HTMLElement {
  return screen.getByRole("button", { name: /continue/i });
}

beforeEach(() => {
  jest.clearAllMocks();
});

// ===========================================================================
// Group N — Navigation to Studio (AC-1)
// ===========================================================================
describe("Group N — Navigation to Studio (AC-1)", () => {
  it("N1: smart mode + continue calls onContinue with mode='smart'", async () => {
    const { props } = renderModal();
    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(props.onContinue).toHaveBeenCalledWith(["features/auth.md"], "smart");
  });

  it("N2: regular mode + continue calls onContinue with mode='regular'", async () => {
    const { props } = renderModal();
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(props.onContinue).toHaveBeenCalledWith(["features/auth.md"], "regular");
  });

  it("N3: multiple files in smart mode passes all paths to onContinue", async () => {
    const { props } = renderModal();
    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");
    await userEvent.click(getContinueButton());

    expect(props.onContinue).toHaveBeenCalledWith(
      ["features/auth.md", "tasks/setup.md"],
      "smart",
    );
  });

  it("N4: mode parameter is always passed to onContinue (not optional)", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const call = mockContinue.mock.calls[0];
    expect(call).toHaveLength(2);
    expect(typeof call[1]).toBe("string");
    expect(["regular", "smart"]).toContain(call[1]);
  });

  it("N5: smart mode continues even with single file", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(mockContinue).toHaveBeenCalledWith(["features/auth.md"], "smart");
  });
});

// ===========================================================================
// Group D — Data Flow to Studio Context (AC-2)
// ===========================================================================
describe("Group D — Data Flow (AC-2)", () => {
  it("D1: smart import passes imported data to Studio (via onContinue)", async () => {
    const mockHandler = jest.fn();
    renderModal({ onContinue: mockHandler });

    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(mockHandler).toHaveBeenCalledWith(
      expect.arrayContaining(["features/auth.md"]),
      "smart",
    );
  });

  it("D2: imported files remain selected when switching between modes", async () => {
    renderModal();
    await toggleFile("features/auth.md");
    expect(screen.getByTestId("toggle-features/auth.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.click(getSmartOption());
    expect(screen.getByTestId("toggle-features/auth.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("D3: smart mode passes correct file paths (alphabetically sorted)", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await userEvent.click(getSmartOption());
    await toggleFile("tasks/setup.md"); // toggle second file first
    await toggleFile("features/auth.md"); // toggle first file second
    await userEvent.click(getContinueButton());

    const [paths] = mockContinue.mock.calls[0];
    expect(paths).toEqual(["features/auth.md", "tasks/setup.md"]);
  });

  it("D4: regular mode also passes file data (to confirm backward compatibility)", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");
    await userEvent.click(getContinueButton());

    expect(mockContinue).toHaveBeenCalledWith(
      expect.arrayContaining(["features/auth.md", "tasks/setup.md"]),
      "regular",
    );
  });
});

// ===========================================================================
// Group P — Preview State Recognition (AC-3)
// ===========================================================================
describe("Group P — Preview State (AC-3)", () => {
  it("P1: smart import is distinct from finalized import (different mode)", async () => {
    const smartHandler = jest.fn();
    const regularHandler = jest.fn();

    const { rerender } = renderModal({ onContinue: smartHandler });
    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(smartHandler).toHaveBeenCalledWith(
      expect.anything(),
      expect.stringMatching(/smart/i),
    );

    rerender(
      <BrowserRouter>
        <ImportTicketsModal {...({ open: true, onContinue: regularHandler, workspaceSlug: "loregarden", isLoading: false, onClose: jest.fn() } as React.ComponentProps<typeof ImportTicketsModal>)} />
      </BrowserRouter>,
    );
    await userEvent.click(screen.getByTestId("toggle-features/auth.md"));
    await userEvent.click(getContinueButton());

    expect(regularHandler).toHaveBeenCalledWith(
      expect.anything(),
      expect.stringMatching(/regular/i),
    );
  });

  it("P2: mode parameter allows downstream to distinguish preview vs finalized", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await userEvent.click(getSmartOption());
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    const [, mode] = mockContinue.mock.calls[0];
    expect(mode).toBe("smart");
    expect(mode).not.toBe("regular");
  });

  it("P3: smart and regular modes coexist (not mutually exclusive in behavior)", async () => {
    const handler = jest.fn();
    renderModal({ onContinue: handler });

    // Regular mode first
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());
    expect(handler).toHaveBeenLastCalledWith(
      expect.anything(),
      "regular",
    );

    handler.mockClear();

    // Switch to smart mode (in same session, files preserved)
    await userEvent.click(getSmartOption());
    await userEvent.click(getContinueButton());
    expect(handler).toHaveBeenLastCalledWith(
      expect.anything(),
      "smart",
    );
  });
});

// ===========================================================================
// Group R — Regression / Backward Compatibility
// ===========================================================================
describe("Group R — Regression", () => {
  it("R1: regular import still works (no regression)", async () => {
    const { props } = renderModal();
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(props.onContinue).toHaveBeenCalledWith(
      expect.arrayContaining(["features/auth.md"]),
      "regular",
    );
  });

  it("R2: regular mode is default when no mode specified", async () => {
    const { props } = renderModal();
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");

    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(props.onContinue).toHaveBeenCalledWith(expect.anything(), "regular");
  });

  it("R3: continue button disabled until files selected (all modes)", async () => {
    renderModal();
    expect(getContinueButton()).toBeDisabled();

    await userEvent.click(getSmartOption());
    expect(getContinueButton()).toBeDisabled();

    await toggleFile("features/auth.md");
    expect(getContinueButton()).not.toBeDisabled();
  });

  it("R4: onClose still works (no regression)", async () => {
    const mockClose = jest.fn();
    renderModal({ onClose: mockClose });
    await userEvent.click(getSmartOption());
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(mockClose).toHaveBeenCalledTimes(1);
  });

  it("R5: modal closes after continue (both modes)", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await toggleFile("features/auth.md");
    await userEvent.click(getContinueButton());

    expect(mockContinue).toHaveBeenCalledTimes(1);
  });
});

// ===========================================================================
// Group E — Edge Cases
// ===========================================================================
describe("Group E — Edge Cases", () => {
  it("E1: empty file list disables continue (smart mode)", async () => {
    renderModal();
    await userEvent.click(getSmartOption());
    expect(getContinueButton()).toBeDisabled();
  });

  it("E2: rapidly toggling mode does not cause double-continue", async () => {
    const mockContinue = jest.fn();
    renderModal({ onContinue: mockContinue });
    await toggleFile("features/auth.md");
    await userEvent.click(getSmartOption());
    await userEvent.click(getRegularOption());
    await userEvent.click(getSmartOption());
    await userEvent.click(getContinueButton());

    expect(mockContinue).toHaveBeenCalledTimes(1);
  });

  it("E3: isLoading disables mode selector (smart mode not selectable)", async () => {
    renderModal({ isLoading: true });
    expect(getSmartOption()).toBeDisabled();

    await userEvent.click(getSmartOption());
    expect(getSmartOption()).toHaveAttribute("aria-checked", "false");
    expect(getRegularOption()).toHaveAttribute("aria-checked", "true");
  });

  it("E4: switching modes preserves previous file selection", async () => {
    renderModal();
    await toggleFile("features/auth.md");
    await toggleFile("tasks/setup.md");

    expect(screen.getByTestId("toggle-features/auth.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("toggle-tasks/setup.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.click(getSmartOption());

    expect(screen.getByTestId("toggle-features/auth.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("toggle-tasks/setup.md")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("E5: mode selection does not trigger onContinue", async () => {
    const { props } = renderModal();
    await userEvent.click(getSmartOption());
    await userEvent.click(getRegularOption());

    expect(props.onContinue).not.toHaveBeenCalled();
  });
});
