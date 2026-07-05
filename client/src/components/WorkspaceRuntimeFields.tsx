import type { ReactNode } from "react";

import type { RuntimeOptions, WorkspaceRuntimeSettings, WorkspaceSummary } from "../api/client";

export function claudeModelEnabled(cliAdapter: string): boolean {
  return ["default", "claude"].includes(cliAdapter || "default");
}

export function cursorModelEnabled(cliAdapter: string): boolean {
  return ["default", "cursor"].includes(cliAdapter || "default");
}

export function lmstudioFieldsEnabled(cliAdapter: string): boolean {
  return (cliAdapter || "default") === "lmstudio";
}

function adapterLabel(options: RuntimeOptions, adapterId: string): string {
  return options.cli_adapters.find((opt) => opt.id === adapterId)?.label ?? adapterId;
}

function modelLabel(
  models: RuntimeOptions["claude_models"],
  modelId: string,
): string {
  if (!modelId) {
    return models.find((opt) => opt.id === "")?.label ?? "Default";
  }
  return models.find((opt) => opt.id === modelId)?.label ?? modelId;
}

export function runtimeSummaryLabel(
  runtime: WorkspaceRuntimeSettings,
  options: RuntimeOptions | undefined,
): string {
  if (!options) return "…";
  const adapterId = runtime.cli_adapter || "default";

  if (adapterId === "default") return "Workspace default";
  if (adapterId === "local") return "Local runner";

  if (adapterId === "claude") {
    const name = modelLabel(options.claude_models, runtime.claude_model ?? "");
    return name.includes("Default") ? "Claude Code" : name;
  }
  if (adapterId === "cursor") {
    const name = modelLabel(options.cursor_models, runtime.cursor_model ?? "");
    return name.includes("Default") ? "Cursor" : name;
  }
  if (adapterId === "lmstudio") {
    return runtime.lmstudio_model?.trim() || "LM Studio";
  }

  return adapterLabel(options, adapterId);
}

function providerNeedsModel(adapter: string): boolean {
  return ["claude", "cursor", "lmstudio"].includes(adapter);
}

interface WorkspaceRuntimeFieldsProps {
  runtime: WorkspaceRuntimeSettings;
  options: RuntimeOptions;
  disabled?: boolean;
  compact?: boolean;
  onChange: (runtime: WorkspaceRuntimeSettings) => void;
}

export function WorkspaceRuntimeFields({
  runtime,
  options,
  disabled = false,
  compact = false,
  onChange,
}: WorkspaceRuntimeFieldsProps) {
  const adapter = runtime.cli_adapter || "default";
  const gap = compact ? 8 : 12;
  const selectStyle = { width: "100%", fontSize: 12 };

  const handleProviderChange = (nextAdapter: string) => {
    onChange({
      ...runtime,
      cli_adapter: nextAdapter,
    });
  };

  let modelStep: ReactNode = null;

  if (adapter === "claude") {
    modelStep = (
      <select
        className="btn-secondary filter-select"
        style={selectStyle}
        value={runtime.claude_model ?? ""}
        disabled={disabled}
        onChange={(e) =>
          onChange({
            ...runtime,
            claude_model: e.target.value,
          })
        }
      >
        {options.claude_models.map((opt) => (
          <option key={opt.id || "default"} value={opt.id}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  } else if (adapter === "cursor") {
    modelStep = (
      <select
        className="btn-secondary filter-select"
        style={selectStyle}
        value={runtime.cursor_model ?? ""}
        disabled={disabled}
        onChange={(e) =>
          onChange({
            ...runtime,
            cursor_model: e.target.value,
          })
        }
      >
        {options.cursor_models.map((opt) => (
          <option key={opt.id || "default"} value={opt.id}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  } else if (adapter === "lmstudio") {
    modelStep = (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input
          className="btn-secondary"
          style={{ ...selectStyle, boxSizing: "border-box" }}
          value={runtime.lmstudio_model ?? ""}
          placeholder="Loaded model id"
          disabled={disabled}
          onChange={(e) =>
            onChange({
              ...runtime,
              lmstudio_model: e.target.value,
            })
          }
        />
        <div className="modal-field" style={{ margin: 0 }}>
          <div className="modal-field-label">Server URL</div>
          <input
            className="btn-secondary"
            style={{ ...selectStyle, boxSizing: "border-box" }}
            value={runtime.lmstudio_base_url ?? ""}
            placeholder="http://127.0.0.1:1234/v1"
            disabled={disabled}
            onChange={(e) =>
              onChange({
                ...runtime,
                lmstudio_base_url: e.target.value,
              })
            }
          />
        </div>
      </div>
    );
  } else if (adapter === "default") {
    modelStep = (
      <p className="modal-hint" style={{ margin: 0 }}>
        Inherits the workspace default from Settings. No model pick needed here.
      </p>
    );
  } else if (adapter === "local") {
    modelStep = (
      <p className="modal-hint" style={{ margin: 0 }}>
        Uses the built-in local test runner. No model pick needed.
      </p>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap }}>
      <div className="modal-field">
        <div className="modal-field-label">1 · Provider</div>
        <select
          className="btn-secondary filter-select"
          style={selectStyle}
          value={adapter}
          disabled={disabled}
          onChange={(e) => handleProviderChange(e.target.value)}
        >
          {options.cli_adapters.map((opt) => (
            <option key={opt.id} value={opt.id}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="modal-field">
        <div className="modal-field-label">
          {providerNeedsModel(adapter) ? "2 · Model" : "2 · Details"}
        </div>
        {modelStep}
      </div>
    </div>
  );
}

export function runtimeFromWorkspace(workspace: WorkspaceSummary | undefined): WorkspaceRuntimeSettings {
  return {
    cli_adapter: workspace?.cli_adapter || "default",
    claude_model: workspace?.claude_model ?? "",
    cursor_model: workspace?.cursor_model ?? "",
    lmstudio_base_url: workspace?.lmstudio_base_url ?? "",
    lmstudio_model: workspace?.lmstudio_model ?? "",
  };
}

export function runtimeSettingsEqual(a: WorkspaceRuntimeSettings, b: WorkspaceRuntimeSettings): boolean {
  return (
    (a.cli_adapter || "default") === (b.cli_adapter || "default") &&
    (a.claude_model ?? "") === (b.claude_model ?? "") &&
    (a.cursor_model ?? "") === (b.cursor_model ?? "") &&
    (a.lmstudio_base_url ?? "") === (b.lmstudio_base_url ?? "") &&
    (a.lmstudio_model ?? "") === (b.lmstudio_model ?? "")
  );
}
