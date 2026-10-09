/**
 * Every modal dialog in the app traps focus — checked against the source tree,
 * not against a list.
 *
 * A per-component test would have to be written twenty-eight times and would
 * still say nothing about the twenty-ninth. The defect this guards is not one
 * modal losing its trap; it is a new modal shipping without one, which is
 * exactly what happened to all twenty-eight of them. So the fixture is the
 * repository: find what renders a dialog over a backdrop, and require it to use
 * the hook.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..", "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return entry === "__tests__" || entry === "node_modules" ? [] : sourceFiles(path);
    }
    return path.endsWith(".tsx") ? [path] : [];
  });
}

/** The dialogs drawn over a backdrop, keyed by path relative to `src/`. */
function modalDialogSources(): { path: string; source: string }[] {
  return sourceFiles(SRC)
    .map((path) => ({ path: path.slice(SRC.length + 1), source: readFileSync(path, "utf8") }))
    .filter(({ source }) => /role="(alert)?dialog"/.test(source))
    .filter(({ source }) => /modal-overlay|modal-backdrop|aria-modal="true"/.test(source));
}

/** Dialogs drawn through ModalShell, which owns the overlay, the trap and Escape. */
function shellDialogSources(): string[] {
  return sourceFiles(SRC)
    .map((path) => ({ path: path.slice(SRC.length + 1), source: readFileSync(path, "utf8") }))
    .filter(({ source }) => source.includes("<ModalShell"))
    .map(({ path }) => path);
}

describe("modal dialogs", () => {
  const dialogs = modalDialogSources();
  const shellDialogs = shellDialogSources();

  it("finds the app's dialogs at all", () => {
    // A scan that quietly matched nothing would pass every assertion below.
    // A dialog on ModalShell no longer spells out role="dialog" itself, so it
    // is counted here and covered by the shell's own entry in the scan.
    expect(dialogs.length + shellDialogs.length).toBeGreaterThan(20);
    expect(dialogs.map((entry) => entry.path)).toContain("components/ui/ModalShell.tsx");
  });

  it.each(dialogs.map(({ path }) => path))("%s traps focus", (path) => {
    const { source } = dialogs.find((entry) => entry.path === path)!;
    expect(source).toContain("useDialogFocusTrap");
  });

  it("leaves anchored, non-modal popovers alone", () => {
    // `QueueSlotTicketPicker` carries `role="dialog"` and no backdrop: it is a
    // menu hanging off a button, the page behind it stays interactive by
    // design, and Tab is how an operator leaves it. Trapping focus there would
    // be a bug of its own, so the scan must not reach it — asserted here rather
    // than kept as an exemption list, which would silently cover whatever
    // drifted into it.
    const picker = "components/QueueSlotTicketPicker.tsx";
    expect(readFileSync(join(SRC, picker), "utf8")).toMatch(/role="dialog"/);
    expect(dialogs.map((entry) => entry.path)).not.toContain(picker);
  });
});
