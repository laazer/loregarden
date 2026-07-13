import { fireEvent, render, screen } from "@testing-library/react";

import type { RuntimeOptions, WorkspaceRuntimeSettings } from "../../api/client";
import { TriageModelModal } from "../TriageModelModal";

const RUNTIME_OPTIONS: RuntimeOptions = {
  cli_adapters: [
    { id: "default", label: "Workspace default" },
    { id: "claude", label: "Claude Code" },
    { id: "cursor", label: "Cursor Agent" },
  ],
  claude_models: [
    { id: "", label: "Default (Claude Code profile)" },
    { id: "opus", label: "Opus" },
  ],
  cursor_models: [
    { id: "", label: "Default (Cursor profile)" },
    { id: "sonnet-4", label: "Sonnet 4" },
  ],
};

const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

describe("TriageModelModal", () => {
  it("renders nothing when closed", () => {
    render(
      <TriageModelModal
        open={false}
        runtime={DEFAULT_RUNTIME}
        runtimeOptions={RUNTIME_OPTIONS}
        isSaving={false}
        onClose={() => {}}
        onSave={async () => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("defaults to the Triage scope label and subtitle", () => {
    render(
      <TriageModelModal
        open
        runtime={DEFAULT_RUNTIME}
        runtimeOptions={RUNTIME_OPTIONS}
        isSaving={false}
        onClose={() => {}}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByText("Triage")).toBeInTheDocument();
    expect(screen.getByText(/pick a model for this ticket$/)).toBeInTheDocument();
  });

  it("renders a custom scope label and subtitle for non-triage usages", () => {
    render(
      <TriageModelModal
        open
        runtime={DEFAULT_RUNTIME}
        runtimeOptions={RUNTIME_OPTIONS}
        isSaving={false}
        onClose={() => {}}
        onSave={async () => {}}
        scopeLabel="Workflow"
        subtitle="Choose a provider, then pick a model for this ticket's agent runs"
      />,
    );
    expect(screen.getByText("Workflow")).toBeInTheDocument();
    expect(
      screen.getByText("Choose a provider, then pick a model for this ticket's agent runs"),
    ).toBeInTheDocument();
  });

  it("disables Save until the draft differs from the saved runtime, then saves and closes", async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    const onClose = jest.fn();
    render(
      <TriageModelModal
        open
        runtime={DEFAULT_RUNTIME}
        runtimeOptions={RUNTIME_OPTIONS}
        isSaving={false}
        onClose={onClose}
        onSave={onSave}
      />,
    );

    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    const [providerSelect] = screen.getAllByRole("combobox");
    fireEvent.change(providerSelect, { target: { value: "claude" } });
    expect(saveButton).toBeEnabled();

    fireEvent.click(saveButton);
    await Promise.resolve();

    expect(onSave).toHaveBeenCalledWith({ ...DEFAULT_RUNTIME, cli_adapter: "claude" });
    expect(onClose).toHaveBeenCalled();
  });

  it("cancel closes without saving", () => {
    const onSave = jest.fn();
    const onClose = jest.fn();
    render(
      <TriageModelModal
        open
        runtime={DEFAULT_RUNTIME}
        runtimeOptions={RUNTIME_OPTIONS}
        isSaving={false}
        onClose={onClose}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });
});
