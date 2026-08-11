import { fireEvent, render, screen } from "@testing-library/react";

import type { RuntimeOptions, WorkspaceRuntimeSettings } from "../../api/client";
import { DEFAULT_RUNTIME } from "../../lib/runtimeSettings";
import { WorkspaceRuntimeFields, runtimeSettingsEqual } from "../WorkspaceRuntimeFields";

const OPTIONS: RuntimeOptions = {
  cli_adapters: [
    { id: "default", label: "Workspace default" },
    { id: "claude", label: "Claude Code" },
    { id: "cursor", label: "Cursor Agent" },
    { id: "codex", label: "Codex CLI" },
  ],
  claude_models: [
    { id: "", label: "Default (Claude Code profile)" },
    { id: "claude-opus-5", label: "Claude Opus 5 (pinned)" },
  ],
  cursor_models: [
    { id: "", label: "Default (Cursor profile)" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { id: "gpt-5", label: "GPT-5" },
  ],
  codex_models: [
    { id: "", label: "Default (Codex profile)" },
    { id: "gpt-5", label: "GPT-5" },
  ],
  claude_efforts: [
    { id: "", label: "Default (Claude Code decides)" },
    { id: "high", label: "High — Claude Code default" },
    { id: "xhigh", label: "Extra high — best for coding/agentic" },
  ],
  cursor_efforts: [
    { id: "", label: "Default (Cursor decides)" },
    { id: "high", label: "High" },
  ],
  cursor_effort_models: ["claude-opus-4-8"],
};

function renderFields(runtime: Partial<WorkspaceRuntimeSettings>, options = OPTIONS) {
  const onChange = jest.fn();
  render(
    <WorkspaceRuntimeFields
      runtime={{ ...DEFAULT_RUNTIME, ...runtime }}
      options={options}
      onChange={onChange}
    />,
  );
  return onChange;
}

test("claude shows an effort select and reports the picked level", () => {
  const onChange = renderFields({ cli_adapter: "claude" });

  const effort = screen.getByLabelText("Reasoning effort");
  fireEvent.change(effort, { target: { value: "xhigh" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ claude_effort: "xhigh" }));
});

test("adapters without an effort control render no effort step", () => {
  renderFields({ cli_adapter: "default" });

  expect(screen.queryByText(/Reasoning effort/)).not.toBeInTheDocument();
});

test("cursor effort is disabled unless the model accepts a bracket override", () => {
  renderFields({ cli_adapter: "cursor", cursor_model: "gpt-5" });

  const effort = screen.getByLabelText("Reasoning effort");
  expect(effort).toBeDisabled();
  expect(screen.getByText(/only on a parameterized model/)).toBeInTheDocument();
});

test("cursor effort is enabled for a parameterized model", () => {
  renderFields({ cli_adapter: "cursor", cursor_model: "claude-opus-4-8" });

  const effort = screen.getByLabelText("Reasoning effort");
  expect(effort).not.toBeDisabled();
});

test("codex shows its model select without an effort control", () => {
  const onChange = renderFields({ cli_adapter: "codex" });

  fireEvent.change(screen.getByLabelText("Codex model"), { target: { value: "gpt-5" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ codex_model: "gpt-5" }));
  expect(screen.queryByText(/Reasoning effort/)).not.toBeInTheDocument();
});

test("the effective line names the resolved run, not the empty pin", () => {
  renderFields(
    { cli_adapter: "default" },
    {
      ...OPTIONS,
      effective: {
        cli_adapter: "claude",
        cli_adapter_source: "global",
        model: "claude-opus-5",
        model_source: "workspace",
        effort: "xhigh",
        effort_source: "env",
        supports_model: true,
        supports_effort: true,
      },
    },
  );

  const summary = screen.getByText(/Currently runs as:/);
  expect(summary).toHaveTextContent("claude-opus-5 (workspace default)");
  expect(summary).toHaveTextContent("xhigh (environment override)");
});

test("an adapter that takes no pins reports only the adapter", () => {
  renderFields(
    { cli_adapter: "default" },
    {
      ...OPTIONS,
      effective: {
        cli_adapter: "local",
        cli_adapter_source: "global",
        model: "",
        model_source: "cli-default",
        effort: "",
        effort_source: "cli-default",
        supports_model: false,
        supports_effort: false,
      },
    },
  );

  const summary = screen.getByText(/Currently runs as:/);
  expect(summary).toHaveTextContent("local (global settings)");
  expect(summary).not.toHaveTextContent("cli-default");
});

test("runtimeSettingsEqual notices an effort-only change", () => {
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, claude_effort: "max" }),
  ).toBe(false);
  expect(runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME })).toBe(true);
});

const OPENCODE_OPTIONS: RuntimeOptions = {
  ...OPTIONS,
  cli_adapters: [...OPTIONS.cli_adapters, { id: "opencode", label: "OpenCode" }],
  opencode_models: [
    { id: "", label: "Default (OpenCode profile)" },
    { id: "opencode/nemotron-3.5-lightning-free", label: "opencode/nemotron-3.5-lightning-free" },
  ],
  opencode_efforts: [
    { id: "", label: "Default (model's own variant)" },
    { id: "high", label: "High" },
  ],
};

test("opencode pins a discovered model", () => {
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByLabelText("OpenCode model"), {
    target: { value: "opencode/nemotron-3.5-lightning-free" },
  });

  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ opencode_model: "opencode/nemotron-3.5-lightning-free" }),
  );
});

test("opencode accepts a model id the picker never discovered", () => {
  // The catalogue depends on which providers OpenCode is signed in to, so the
  // select is a shortcut rather than the set of legal values.
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByPlaceholderText("Or type a provider/model id"), {
    target: { value: "anthropic/claude-opus-5" },
  });

  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ opencode_model: "anthropic/claude-opus-5" }),
  );
});

test("opencode reports its effort level as a variant pin", () => {
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByLabelText("Reasoning effort"), { target: { value: "high" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ opencode_effort: "high" }));
});

test("runtimeSettingsEqual notices an opencode-only change", () => {
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, opencode_model: "a/b" }),
  ).toBe(false);
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, opencode_effort: "max" }),
  ).toBe(false);
});
