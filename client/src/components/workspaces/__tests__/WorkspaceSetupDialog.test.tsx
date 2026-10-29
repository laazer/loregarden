import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";

import { WorkspaceSetupDialog } from "../WorkspaceSetupDialog";

jest.mock("../../instances/WorkspaceIntegrationPanel", () => ({ WorkspaceIntegrationPanel: () => null }));
jest.mock("../../instances/WorkspaceTemplatesPanel", () => ({ WorkspaceTemplatesPanel: () => null }));
// Stands in for the presets panel with a tick waiting to be saved.
jest.mock("../WorkspaceGatePresetsPanel", () => ({
  WorkspaceGatePresetsPanel: ({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) => {
    useEffect(() => onDirtyChange(true), [onDirtyChange]);
    return null;
  },
}));

const workspace = { slug: "blobert", name: "Blobert", repo_root: "/w/blobert", entries: [] } as never;

describe("WorkspaceSetupDialog", () => {
  it("asks before closing over unsaved gate ticks, and stays open on No", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
    const onClose = jest.fn();
    render(<WorkspaceSetupDialog workspace={workspace} onClose={onClose} />);

    await userEvent.keyboard("{Escape}");

    expect(confirm).toHaveBeenCalledWith("Discard Blobert's unsaved gate changes?");
    expect(onClose).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });
});
