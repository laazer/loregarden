import type { GateKind, GatePresets, ToolchainPreset } from "../../api/gatePresetTypes";
import "./GatePresetPicker.css";

const KIND_LABEL: Record<GateKind, string> = {
  lint: "lint",
  format: "format",
  typecheck: "types",
  test: "tests",
};

interface GatePresetPickerProps {
  presets: GatePresets;
  selected: ReadonlySet<string>;
  onToggle: (command: string, on: boolean) => void;
  disabled: boolean;
  /** Prefix for element ids, unique per picker on the page. */
  idPrefix: string;
  /** Preset → the configured command that already does it; shown when spelled differently. */
  runningAs?: ReadonlyMap<string, string>;
}

function Toolchain({
  toolchain,
  selected,
  onToggle,
  disabled,
  id,
  runningAs,
}: {
  toolchain: ToolchainPreset;
  selected: ReadonlySet<string>;
  onToggle: (command: string, on: boolean) => void;
  disabled: boolean;
  id: string;
  runningAs?: ReadonlyMap<string, string>;
}) {
  return (
    <fieldset className="gate-preset-toolchain" disabled={disabled}>
      <legend>
        {toolchain.label}
        {toolchain.detected && (
          <span className="instances-meta"> — {toolchain.directory === "." ? "repository root" : `${toolchain.directory}/`}</span>
        )}
      </legend>
      {toolchain.commands.map((preset, index) => {
        const inputId = `${id}-${index}`;
        return (
          <div key={preset.command} className="gate-preset-row">
            <input
              id={inputId}
              type="checkbox"
              checked={selected.has(preset.command)}
              onChange={(e) => onToggle(preset.command, e.target.checked)}
            />
            <label htmlFor={inputId}>
              {preset.label} <span className="instances-chip instances-chip--muted">{KIND_LABEL[preset.kind]}</span>
              <code className="gate-preset-command">{preset.command}</code>
              {runningAs?.get(preset.command) && runningAs.get(preset.command) !== preset.command ? (
                <span className="gate-preset-running-as">
                  Already runs as <code>{runningAs.get(preset.command)}</code>
                </span>
              ) : null}
            </label>
          </div>
        );
      })}
      {toolchain.note && <p className="instances-meta">{toolchain.note}</p>}
    </fieldset>
  );
}

/**
 * Tick the gate commands a workspace's toolchains should run on every stage transition.
 *
 * Detected toolchains come first, their fast checks ticked; tests start unticked
 * because a slow suite on every transition is a timeout on every transition. The
 * rest are folded under "Other toolchains" — open by default when nothing was
 * detected, which is the brand-new-repository case, so the stack can be picked
 * before any code exists.
 */
export function GatePresetPicker({ presets, selected, onToggle, disabled, idPrefix, runningAs }: GatePresetPickerProps) {
  const detected = presets.toolchains.filter((t) => t.detected);
  const others = presets.toolchains.filter((t) => !t.detected);
  const render = (toolchain: ToolchainPreset, index: number, group: string) => (
    <Toolchain
      key={`${toolchain.key}:${toolchain.directory}`}
      toolchain={toolchain}
      selected={selected}
      onToggle={onToggle}
      disabled={disabled}
      id={`${idPrefix}-${group}-${index}`}
      runningAs={runningAs}
    />
  );

  return (
    <div className="gate-preset-picker">
      {detected.length === 0 && (
        <p className="instances-meta">Nothing detected here yet. Pick the toolchain this project will use.</p>
      )}
      {detected.map((toolchain, index) => render(toolchain, index, "detected"))}
      {others.length > 0 && (
        <details open={detected.length === 0}>
          <summary>Other toolchains ({others.length})</summary>
          {others.map((toolchain, index) => render(toolchain, index, "other"))}
        </details>
      )}
    </div>
  );
}
