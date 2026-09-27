import { useEffect, useRef, useState } from "react";

import type { GateTestCommandResult } from "../../api/types";
import {
  expandGateCommand,
  gateCommandLabel,
  hasUnbalancedQuotes,
  unknownPlaceholders,
} from "./gateCommand";

export type DraftCommand = { id: number; value: string };

const OUTCOME_LABEL: Record<GateTestCommandResult["outcome"], string> = {
  passed: "Passed",
  failed: "Failed",
  unavailable: "Couldn't run",
  skipped: "Skipped",
  disabled: "Disabled",
};

function rowsFor(value: string): number {
  return Math.min(5, Math.max(1, Math.ceil(value.length / 78)));
}

function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Where focus goes after a move or remove re-renders the list. */
type FocusRequest = { selector: string } | null;

export function GateCommandList({
  noun,
  commands,
  onChange,
  onFieldFocus,
  newId,
  placeholders,
  results,
  testing = false,
  emptyText,
  inputPlaceholder,
}: {
  /** What one row is called, for button names: "check", "fixer". */
  noun: string;
  commands: DraftCommand[];
  onChange: (next: DraftCommand[]) => void;
  /** Reports the textarea the user is typing in, so placeholder chips can insert into it. */
  onFieldFocus?: (id: number, el: HTMLTextAreaElement) => void;
  newId: () => number;
  placeholders: Record<string, string>;
  /** Test results keyed by the exact command text they ran; an edited row has none. */
  results?: Map<string, GateTestCommandResult>;
  testing?: boolean;
  emptyText: string;
  inputPlaceholder: string;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest>(null);

  useEffect(() => {
    if (!focusRequest) return;
    const target = listRef.current?.querySelector<HTMLElement>(focusRequest.selector);
    (target ?? addRef.current)?.focus();
    setFocusRequest(null);
  }, [focusRequest]);

  const update = (id: number, value: string) =>
    onChange(commands.map((c) => (c.id === id ? { ...c, value } : c)));

  const move = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    const next = [...commands];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
    const edge = target === 0 || target === commands.length - 1;
    const dir = delta < 0 ? "up" : "down";
    // At the edge the pressed button disables; hand focus to its sibling instead.
    const button = edge ? (dir === "up" ? "down" : "up") : dir;
    setFocusRequest({ selector: `[data-command-id="${commands[index].id}"] [data-move="${button}"]` });
  };

  const remove = (index: number) => {
    const neighbour = commands[index + 1] ?? commands[index - 1];
    onChange(commands.filter((_, i) => i !== index));
    setFocusRequest({
      selector: neighbour ? `[data-command-id="${neighbour.id}"] textarea` : "[data-none]",
    });
  };

  const add = () => {
    const id = newId();
    onChange([...commands, { id, value: "" }]);
    setFocusRequest({ selector: `[data-command-id="${id}"] textarea` });
  };

  return (
    <div className="gate-list">
      {commands.length === 0 ? (
        <p className="gate-list-empty">{emptyText}</p>
      ) : (
        <ol className="gate-list-rows" ref={listRef}>
          {commands.map((command, index) => (
            <GateCommandRow
              key={command.id}
              noun={noun}
              index={index}
              count={commands.length}
              command={command}
              placeholders={placeholders}
              result={results?.get(command.value.trim())}
              testing={testing && command.value.trim() !== ""}
              inputPlaceholder={inputPlaceholder}
              onChange={(value) => update(command.id, value)}
              onFocus={(el) => onFieldFocus?.(command.id, el)}
              onMove={(delta) => move(index, delta)}
              onRemove={() => remove(index)}
            />
          ))}
        </ol>
      )}
      <button type="button" className="btn-secondary gate-list-add" ref={addRef} onClick={add}>
        + Add {noun}
      </button>
    </div>
  );
}

function GateCommandRow({
  noun,
  index,
  count,
  command,
  placeholders,
  result,
  testing,
  inputPlaceholder,
  onChange,
  onFocus,
  onMove,
  onRemove,
}: {
  noun: string;
  index: number;
  count: number;
  command: DraftCommand;
  placeholders: Record<string, string>;
  result?: GateTestCommandResult;
  testing: boolean;
  inputPlaceholder: string;
  onChange: (value: string) => void;
  onFocus: (el: HTMLTextAreaElement) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const value = command.value;
  const blank = value.trim() === "";
  const unknown = unknownPlaceholders(value, placeholders);
  const unclosed = hasUnbalancedQuotes(value);
  const label = blank ? `New ${noun}` : gateCommandLabel(value);
  const position = `${noun} ${index + 1}`;
  const output = result ? [result.stderr, result.stdout].filter(Boolean).join("\n") : "";
  const detail = output || (result && result.outcome !== "passed" ? result.message : "");

  return (
    <li className="gate-row" data-command-id={command.id}>
      <div className="gate-row-head">
        <span className="gate-row-index" aria-hidden>
          {index + 1}
        </span>
        <span className="gate-row-label" title={value}>
          {label}
        </span>
        {testing ? (
          <span className="gate-badge gate-badge--running">Testing…</span>
        ) : result ? (
          <span className={`gate-badge gate-badge--${result.outcome}`}>
            {OUTCOME_LABEL[result.outcome]} · {formatDuration(result.duration_ms)}
          </span>
        ) : null}
        <div className="gate-row-tools">
          <button
            type="button"
            className="gate-icon-btn"
            data-move="up"
            aria-label={`Move ${position} up`}
            title="Move up"
            disabled={index === 0}
            onClick={() => onMove(-1)}
          >
            ↑
          </button>
          <button
            type="button"
            className="gate-icon-btn"
            data-move="down"
            aria-label={`Move ${position} down`}
            title="Move down"
            disabled={index === count - 1}
            onClick={() => onMove(1)}
          >
            ↓
          </button>
          <button
            type="button"
            className="gate-icon-btn gate-icon-btn--danger"
            aria-label={`Remove ${position}`}
            title="Remove"
            onClick={onRemove}
          >
            ✕
          </button>
        </div>
      </div>

      <textarea
        className="gate-row-input"
        aria-label={`${position} command`}
        spellCheck={false}
        rows={rowsFor(value)}
        value={value}
        placeholder={inputPlaceholder}
        onFocus={(e) => onFocus(e.currentTarget)}
        onChange={(e) => onChange(e.target.value.replace(/\r?\n/g, " "))}
        onKeyDown={(e) => {
          // A command is one line; Enter must not smuggle a newline into it.
          if (e.key === "Enter") e.preventDefault();
        }}
      />

      {unclosed && (
        <p className="gate-row-warn gate-row-warn--error" role="alert">
          A quote is never closed, so this {noun} can't run — every transition would stop for you.
        </p>
      )}
      {unknown.length > 0 && (
        <p className="gate-row-warn">
          Unknown placeholder{unknown.length > 1 ? "s" : ""}{" "}
          {unknown.map((name) => `{${name}}`).join(", ")} — the {noun} will receive the braces
          literally.
        </p>
      )}

      {!blank && value.includes("{") && (
        <details className="gate-row-details">
          <summary>Expanded with sample values</summary>
          <code className="gate-row-code">{expandGateCommand(value, placeholders)}</code>
        </details>
      )}
      {detail && (
        <details className="gate-row-details" open={result?.outcome !== "passed"}>
          <summary>Test output</summary>
          <pre className="gate-row-output">{detail}</pre>
        </details>
      )}
    </li>
  );
}
