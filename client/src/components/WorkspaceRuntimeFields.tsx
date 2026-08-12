import type { ReactNode } from "react";

import type {
  RuntimeEffective,
  RuntimeOption,
  RuntimeOptions,
  WorkspaceRuntimeSettings,
  WorkspaceSummary,
} from "../api/client";

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
  if (adapterId === "local") return "Local stub";

  if (adapterId === "claude") {
    const name = modelLabel(options.claude_models, runtime.claude_model ?? "");
    return name.includes("Default") ? "Claude Code" : name;
  }
  if (adapterId === "cursor") {
    const name = modelLabel(options.cursor_models, runtime.cursor_model ?? "");
    return name.includes("Default") ? "Cursor" : name;
  }
  if (adapterId === "codex") {
    const name = modelLabel(options.codex_models ?? [], runtime.codex_model ?? "");
    return name.includes("Default") ? "Codex" : name;
  }
  if (adapterId === "lmstudio") {
    return runtime.lmstudio_model?.trim() || "LM Studio";
  }
  if (adapterId === "opencode") {
    return runtime.opencode_model?.trim() || "OpenCode";
  }

  return adapterLabel(options, adapterId);
}

function providerNeedsModel(adapter: string): boolean {
  return ["claude", "cursor", "codex", "lmstudio", "opencode"].includes(adapter);
}

type EffortKey =
  | "claude_effort"
  | "cursor_effort"
  | "lmstudio_effort"
  | "opencode_effort";

const EFFORT_FIELD: Record<string, EffortKey> = {
  claude: "claude_effort",
  cursor: "cursor_effort",
  lmstudio: "lmstudio_effort",
  opencode: "opencode_effort",
};

function effortOptions(options: RuntimeOptions, adapter: string): RuntimeOption[] {
  if (adapter === "claude") return options.claude_efforts ?? [];
  if (adapter === "cursor") return options.cursor_efforts ?? [];
  if (adapter === "lmstudio") return options.lmstudio_efforts ?? [];
  if (adapter === "opencode") return options.opencode_efforts ?? [];
  return [];
}

/** Cursor has no `--effort` flag — the level rides along as a bracket parameter
 *  on a parameterized model id, so the control is meaningless for the rest. */
function cursorEffortApplies(options: RuntimeOptions, model: string): boolean {
  return (options.cursor_effort_models ?? []).includes(model);
}

const SOURCE_LABEL: Record<string, string> = {
  env: "environment override",
  ticket: "this ticket",
  workspace: "workspace default",
  global: "global settings",
  "cli-default": "the CLI's own default",
};

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

export function effectiveRuntimeSummary(effective: RuntimeEffective): string {
  const parts = [
    `${effective.cli_adapter} (${sourceLabel(effective.cli_adapter_source)})`,
  ];
  if (effective.supports_model) {
    const model = effective.model || "model chosen by the CLI";
    parts.push(`${model} (${sourceLabel(effective.model_source)})`);
  }
  if (effective.supports_effort) {
    const effort = effective.effort || "effort chosen by the CLI";
    parts.push(`${effort} (${sourceLabel(effective.effort_source)})`);
  }
  return parts.join(" · ");
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
        aria-label="Claude model"
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
        aria-label="Cursor model"
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
  } else if (adapter === "codex") {
    modelStep = (
      <select
        className="btn-secondary filter-select"
        style={selectStyle}
        aria-label="Codex model"
        value={runtime.codex_model ?? ""}
        disabled={disabled}
        onChange={(e) =>
          onChange({
            ...runtime,
            codex_model: e.target.value,
          })
        }
      >
        {(options.codex_models ?? [{ id: "", label: "Default (Codex profile)" }]).map((opt) => (
          <option key={opt.id || "default"} value={opt.id}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  } else if (adapter === "lmstudio") {
    const lmOptions = options.lmstudio_models ?? [];
    modelStep = (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {lmOptions.length > 1 ? (
          <select
            className="btn-secondary filter-select"
            style={selectStyle}
            value={
              lmOptions.some((opt) => opt.id === (runtime.lmstudio_model ?? ""))
                ? (runtime.lmstudio_model ?? "")
                : ""
            }
            disabled={disabled}
            onChange={(e) =>
              onChange({
                ...runtime,
                lmstudio_model: e.target.value,
              })
            }
          >
            {lmOptions.map((opt) => (
              <option key={opt.id || "auto"} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        ) : null}
        <input
          className="btn-secondary"
          style={{ ...selectStyle, boxSizing: "border-box" }}
          value={runtime.lmstudio_model ?? ""}
          placeholder={
            lmOptions.length > 1
              ? "Or type a model id"
              : "Loaded model id (start LM Studio to discover)"
          }
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
  } else if (adapter === "opencode") {
    const ocOptions = options.opencode_models ?? [];
    modelStep = (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {ocOptions.length > 1 ? (
          <select
            className="btn-secondary filter-select"
            style={selectStyle}
            aria-label="OpenCode model"
            value={
              ocOptions.some((opt) => opt.id === (runtime.opencode_model ?? ""))
                ? (runtime.opencode_model ?? "")
                : ""
            }
            disabled={disabled}
            onChange={(e) =>
              onChange({
                ...runtime,
                opencode_model: e.target.value,
              })
            }
          >
            {ocOptions.map((opt) => (
              <option key={opt.id || "default"} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        ) : null}
        {/* The catalogue depends on which providers OpenCode is authenticated
            to, so a model the picker has not discovered is still valid to pin. */}
        <input
          className="btn-secondary"
          style={{ ...selectStyle, boxSizing: "border-box" }}
          value={runtime.opencode_model ?? ""}
          placeholder={
            ocOptions.length > 1
              ? "Or type a provider/model id"
              : "provider/model id (install OpenCode to discover)"
          }
          disabled={disabled}
          onChange={(e) =>
            onChange({
              ...runtime,
              opencode_model: e.target.value,
            })
          }
        />
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
        Dev stub only — does not call a real model. Choose LM Studio for local LLMs.
      </p>
    );
  }

  const efforts = effortOptions(options, adapter);
  const effortKey = EFFORT_FIELD[adapter];
  const cursorEffortInert =
    adapter === "cursor" && !cursorEffortApplies(options, runtime.cursor_model ?? "");
  let effortStep: ReactNode = null;

  if (effortKey && efforts.length > 0) {
    effortStep = (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <select
          className="btn-secondary filter-select"
          aria-label="Reasoning effort"
          style={selectStyle}
          value={runtime[effortKey] ?? ""}
          disabled={disabled || cursorEffortInert}
          onChange={(e) => onChange({ ...runtime, [effortKey]: e.target.value })}
        >
          {efforts.map((opt) => (
            <option key={opt.id || "default"} value={opt.id}>
              {opt.label}
            </option>
          ))}
        </select>
        {cursorEffortInert ? (
          <p className="modal-hint" style={{ margin: 0 }}>
            Cursor takes effort only on a parameterized model
            {(options.cursor_effort_models ?? []).length > 0
              ? ` (${(options.cursor_effort_models ?? []).join(", ")})`
              : ""}
            . Pick one above to set a level.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap }}>
      <div className="modal-field">
        <div className="modal-field-label">1 · Provider</div>
        <select
          className="btn-secondary filter-select"
          aria-label="Provider"
          style={selectStyle}
          value={adapter}
          disabled={disabled}
          onChange={(e) => handleProviderChange(e.target.value)}
        >
          {options.cli_adapters.map((opt) => {
            const missing = opt.available === false;
            return (
              <option key={opt.id} value={opt.id} disabled={missing}>
                {missing ? `${opt.label} — not installed` : opt.label}
              </option>
            );
          })}
        </select>
      </div>

      <div className="modal-field">
        <div className="modal-field-label">
          {providerNeedsModel(adapter) ? "2 · Model" : "2 · Details"}
        </div>
        {modelStep}
      </div>

      {effortStep ? (
        <div className="modal-field">
          <div className="modal-field-label">3 · Reasoning effort</div>
          {effortStep}
        </div>
      ) : null}

      {options.effective ? (
        <p className="modal-hint" style={{ margin: 0 }}>
          Currently runs as: {effectiveRuntimeSummary(options.effective)}
        </p>
      ) : null}
    </div>
  );
}

export function runtimeFromWorkspace(workspace: WorkspaceSummary | undefined): WorkspaceRuntimeSettings {
  return {
    cli_adapter: workspace?.cli_adapter || "default",
    claude_model: workspace?.claude_model ?? "",
    cursor_model: workspace?.cursor_model ?? "",
    codex_model: workspace?.codex_model ?? "",
    lmstudio_base_url: workspace?.lmstudio_base_url ?? "",
    lmstudio_model: workspace?.lmstudio_model ?? "",
    opencode_model: workspace?.opencode_model ?? "",
    claude_effort: workspace?.claude_effort ?? "",
    cursor_effort: workspace?.cursor_effort ?? "",
    lmstudio_effort: workspace?.lmstudio_effort ?? "",
    opencode_effort: workspace?.opencode_effort ?? "",
  };
}

export function runtimeSettingsEqual(a: WorkspaceRuntimeSettings, b: WorkspaceRuntimeSettings): boolean {
  return (
    (a.cli_adapter || "default") === (b.cli_adapter || "default") &&
    (a.claude_model ?? "") === (b.claude_model ?? "") &&
    (a.cursor_model ?? "") === (b.cursor_model ?? "") &&
    (a.codex_model ?? "") === (b.codex_model ?? "") &&
    (a.lmstudio_base_url ?? "") === (b.lmstudio_base_url ?? "") &&
    (a.lmstudio_model ?? "") === (b.lmstudio_model ?? "") &&
    (a.opencode_model ?? "") === (b.opencode_model ?? "") &&
    (a.claude_effort ?? "") === (b.claude_effort ?? "") &&
    (a.cursor_effort ?? "") === (b.cursor_effort ?? "") &&
    (a.lmstudio_effort ?? "") === (b.lmstudio_effort ?? "") &&
    (a.opencode_effort ?? "") === (b.opencode_effort ?? "")
  );
}
