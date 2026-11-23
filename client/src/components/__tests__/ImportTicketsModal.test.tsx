import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImportTicketsModal, type ImportTicketsModalProps } from "../ImportTicketsModal";

// Paths and repo paths sort differently, so neither can stand in for the other.
jest.mock("../ImportTicketFileExplorer", () => {
  const FILES = [
    { path: "/repo/z/a.md", name: "a.md", repo_path: "a.md" },
    { path: "/repo/y/b.md", name: "b.md", repo_path: "b.md" },
    { path: "/repo/x/c.md", name: "c.md", repo_path: "c.md" },
  ];
  type File = (typeof FILES)[number];

  return {
    __esModule: true,
    ImportTicketFileExplorer: (props: {
      selectedFiles: Map<string, File>;
      onToggleFile: (file: File, checked: boolean) => void;
      onToggleFiles: (files: File[], checked: boolean) => void;
      disabled: boolean;
      startPath: string;
    }) => (
      <div data-testid="mock-file-explorer" data-start-path={props.startPath}>
        {FILES.map((file) => {
          const checked = props.selectedFiles.has(file.path);
          return (
            <button
              key={file.path}
              type="button"
              data-testid={`toggle-${file.repo_path}`}
              aria-pressed={checked}
              disabled={props.disabled}
              onClick={() => props.onToggleFile(file, !checked)}
            >
              {file.repo_path}
            </button>
          );
        })}
        <button type="button" onClick={() => props.onToggleFiles(FILES, true)}>
          Select folder
        </button>
        <button type="button" onClick={() => props.onToggleFiles(FILES.slice(0, 2), false)}>
          Clear two
        </button>
      </div>
    ),
  };
});

function renderModal(overrides: Partial<ImportTicketsModalProps> = {}) {
  const props: ImportTicketsModalProps = {
    open: true,
    workspaceSlug: "loregarden",
    isLoading: false,
    onClose: jest.fn(),
    onContinue: jest.fn(),
    ...overrides,
  };
  const utils = render(<ImportTicketsModal {...props} />);
  const update = (next: Partial<ImportTicketsModalProps>) =>
    utils.rerender(<ImportTicketsModal {...props} {...next} />);
  return { props, update };
}

const toggle = (file: string) => userEvent.click(screen.getByTestId(`toggle-${file}`));
const regular = () => screen.getByRole("radio", { name: "Regular import" });
const smart = () => screen.getByRole("radio", { name: "Smart import" });
const continueButton = () => screen.getByRole("button", { name: /continue|reading files/i });
const selectedList = () => screen.getByRole("list", { name: "Selected ticket files" });
const selectedNames = () => within(selectedList()).getAllByRole("listitem").map((li) => li.textContent);

function expectMode(mode: "regular" | "smart") {
  expect(regular()).toHaveAttribute("aria-checked", String(mode === "regular"));
  expect(smart()).toHaveAttribute("aria-checked", String(mode === "smart"));
}

describe("the import mode", () => {
  it.each([
    [undefined, "regular"],
    ["regular", "regular"],
    ["smart", "smart"],
    [null, "regular"],
    ["nonsense", "regular"],
  ] as const)("opens on initialMode %p as %s", (initialMode, expected) => {
    renderModal({ initialMode: initialMode as ImportTicketsModalProps["initialMode"] });
    expect(within(screen.getByRole("radiogroup", { name: "Import mode" })).getAllByRole("radio")).toHaveLength(2);
    expectMode(expected);
  });

  it("is chosen by click, keeps the files picked, and starts nothing", async () => {
    const { props } = renderModal();
    await toggle("a.md");

    await userEvent.click(smart());
    expectMode("smart");
    await userEvent.click(regular());
    expectMode("regular");
    expect(selectedNames()).toEqual(["a.md"]);
    expect(props.onContinue).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it.each(["{ArrowRight}", "{ArrowDown}", "{ArrowLeft}", "{ArrowUp}"])(
    "moves both ways with %s, and focus moves with it",
    async (key) => {
      renderModal();
      regular().focus();

      await userEvent.keyboard(key);
      expectMode("smart");
      expect(smart()).toHaveFocus();

      await userEvent.keyboard(key);
      expectMode("regular");
      expect(regular()).toHaveFocus();
    },
  );

  it("is one tab stop, on the checked option", async () => {
    renderModal();
    expect(regular()).toHaveAttribute("tabindex", "0");
    expect(smart()).toHaveAttribute("tabindex", "-1");

    await userEvent.click(smart());
    expect(regular()).toHaveAttribute("tabindex", "-1");
    expect(smart()).toHaveAttribute("tabindex", "0");
  });

  it("ignores other keys", async () => {
    renderModal();
    regular().focus();
    await userEvent.keyboard("x");
    expectMode("regular");
  });

  it("is locked while files are read", () => {
    renderModal({ isLoading: true });
    expect(regular()).toBeDisabled();
    expect(smart()).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("radiogroup"), { key: "ArrowRight" });
    expectMode("regular");
  });

  it("is described by text that exists", () => {
    renderModal();
    expect(smart()).toHaveAccessibleDescription(/smart import includes/i);
    expect(regular()).toHaveAccessibleDescription(/smart import includes/i);
  });

  it("is not offered when the caller locks it, and the locked mode is what is sent", async () => {
    const { props } = renderModal({ lockMode: true, initialMode: "smart" });
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.getByText(/open as a scoping session in ticket studio/i)).toBeInTheDocument();

    await toggle("a.md");
    await userEvent.click(continueButton());
    expect(props.onContinue).toHaveBeenCalledWith(["/repo/z/a.md"], "smart");
  });

  it("promises a scoping session only where the mode is locked to one", () => {
    renderModal({ initialMode: "smart" });
    expect(screen.queryByText(/open as a scoping session/i)).not.toBeInTheDocument();
  });
});

describe("the files picked", () => {
  it("are listed and counted as they are toggled, in repo-path order", async () => {
    renderModal();
    expect(continueButton()).toBeDisabled();
    expect(continueButton()).toHaveTextContent("Continue with 0 files");

    await toggle("b.md");
    expect(continueButton()).toHaveTextContent("Continue with 1 file");
    expect(continueButton()).toHaveTextContent(/file$/);
    await toggle("a.md");
    expect(selectedNames()).toEqual(["a.md", "b.md"]);
    expect(screen.getByText("Selected (2)")).toBeInTheDocument();
    expect(continueButton()).toHaveTextContent("Continue with 2 files");

    await toggle("a.md");
    await toggle("b.md");
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();
    expect(continueButton()).toBeDisabled();
  });

  it("can be added and removed a folder at a time", async () => {
    renderModal();
    await userEvent.click(screen.getByRole("button", { name: "Select folder" }));
    expect(selectedNames()).toEqual(["a.md", "b.md", "c.md"]);
    await userEvent.click(screen.getByRole("button", { name: "Clear two" }));
    expect(selectedNames()).toEqual(["c.md"]);
  });

  it("start from the path the caller gives", () => {
    renderModal({ initialBrowsePath: "/repo/docs" });
    expect(screen.getByTestId("mock-file-explorer")).toHaveAttribute("data-start-path", "/repo/docs");
  });

  it("are cleared, and the mode reset, each time the dialog opens", async () => {
    const { update } = renderModal();
    await toggle("a.md");
    await userEvent.click(smart());

    update({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    update({ open: true });
    expect(screen.queryByText(/selected \(/i)).not.toBeInTheDocument();
    expectMode("regular");
  });

  it("survive a change of error, loading or workspace while it is open", async () => {
    const { update } = renderModal();
    await toggle("a.md");
    await userEvent.click(smart());

    update({ isLoading: true });
    update({ errorMessage: "Could not read directory", workspaceSlug: "blobert" });
    expect(selectedNames()).toEqual(["a.md"]);
    expectMode("smart");
  });
});

describe("continuing", () => {
  it.each(["regular", "smart"] as const)("sends the paths in repo-path order, and %s mode", async (mode) => {
    const { props } = renderModal();
    await toggle("c.md");
    await toggle("a.md");
    await toggle("b.md");
    if (mode === "smart") await userEvent.click(smart());

    await userEvent.click(continueButton());
    expect((props.onContinue as jest.Mock).mock.calls).toEqual([
      [["/repo/z/a.md", "/repo/y/b.md", "/repo/x/c.md"], mode],
    ]);
  });

  it("sends once however often it is clicked", async () => {
    const { props } = renderModal();
    await toggle("a.md");
    await userEvent.tripleClick(continueButton());
    expect(props.onContinue).toHaveBeenCalledTimes(1);
    expect(continueButton()).toBeDisabled();
  });

  it("can be sent again after the dialog is reopened", async () => {
    const { props, update } = renderModal();
    await toggle("a.md");
    await userEvent.click(continueButton());

    update({ open: false });
    update({ open: true });
    await toggle("a.md");
    await userEvent.click(continueButton());
    expect(props.onContinue).toHaveBeenCalledTimes(2);
  });

  it("can be sent again once the caller offers a new handler", async () => {
    const retry = jest.fn();
    const { update } = renderModal();
    await toggle("a.md");
    await userEvent.click(continueButton());

    update({ onContinue: retry });
    await userEvent.click(continueButton());
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["throws", () => { throw new Error("no"); }],
    ["rejects", () => Promise.reject(new Error("no"))],
  ])("stays open with its files when the handler %s", async (_how, handler) => {
    const unhandled = jest.fn();
    process.on("unhandledRejection", unhandled);
    const uncaught = jest.fn((event: ErrorEvent) => event.preventDefault());
    window.addEventListener("error", uncaught);
    try {
      const { props } = renderModal({ onContinue: handler });
      await toggle("a.md");
      await userEvent.click(continueButton());
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(unhandled).not.toHaveBeenCalled();
      expect(uncaught).not.toHaveBeenCalled();
      expect(selectedNames()).toEqual(["a.md"]);
      expect(props.onClose).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", unhandled);
      window.removeEventListener("error", uncaught);
    }
  });

  it("shows the caller's error as text", () => {
    renderModal({ errorMessage: "<img src=x> unreadable" });
    expect(screen.getByText("<img src=x> unreadable")).toBeInTheDocument();
  });
});

describe("leaving", () => {
  const overlay = () => document.querySelector(".modal-overlay") as HTMLElement;

  it.each([
    ["Cancel", () => userEvent.click(screen.getByRole("button", { name: "Cancel" }))],
    ["the close button", () => userEvent.click(screen.getByRole("button", { name: /close/i }))],
    ["the backdrop", () => userEvent.click(overlay())],
    ["Escape", () => userEvent.keyboard("{Escape}")],
  ])("closes by %s", async (_by, leave) => {
    const { props } = renderModal();
    await leave();
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("is refused while files are read, and everything but the list is disabled", async () => {
    const { props, update } = renderModal();
    await toggle("a.md");
    update({ isLoading: true });

    await userEvent.click(overlay());
    await userEvent.keyboard("{Escape}");
    expect(props.onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /close/i })).toBeDisabled();
    expect(screen.getByTestId("toggle-a.md")).toBeDisabled();
    expect(continueButton()).toHaveTextContent("Reading files…");
    expect(continueButton()).toBeDisabled();
  });

  it("renders no dialog until opened", () => {
    renderModal({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
