import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type GateTestCommandResult,
  type GateTestReport,
  type OrchestrationProfileView,
} from "../../api/client";
import { describeError } from "../../state/toastStore";
import { GateCommandList, type DraftCommand } from "./GateCommandList";
import { GateStatusHeader } from "./GateStatusHeader";
import { PLACEHOLDER_HELP, gateCommandLabel, hasUnbalancedQuotes, insertAt } from "./gateCommand";
import "./WorkspaceGatesPanel.css";

type Draft = {
  enabled: boolean;
  commands: DraftCommand[];
  transitionScript: string;
  autofixCommands: DraftCommand[];
  agentFallback: boolean;
  maxAttempts: number;
};

const DEFAULT_SCRIPT = "ci/scripts/run_workflow_transition_gates.py";

let nextId = 1;
const newId = () => nextId++;
const toDraftCommands = (values: string[]) => values.map((value) => ({ id: newId(), value }));
const cleaned = (commands: DraftCommand[]) =>
  commands.map((c) => c.value.trim()).filter(Boolean);

function draftFrom(profile: OrchestrationProfileView): Draft {
  return {
    enabled: profile.gates_configured,
    commands: toDraftCommands(profile.gates_commands),
    transitionScript: profile.gates_transition_script,
    autofixCommands: toDraftCommands(profile.gates_autofix_commands),
    agentFallback: profile.gates_autofix_agent_fallback,
    maxAttempts: profile.gates_autofix_max_agent_attempts,
  };
}

/** What would be saved — the comparison key for "unsaved changes". */
function payloadOf(draft: Draft) {
  return {
    enabled: draft.enabled,
    commands: cleaned(draft.commands),
    transition_script: draft.transitionScript.trim(),
    autofix_commands: cleaned(draft.autofixCommands),
    autofix_agent_fallback: draft.agentFallback,
    autofix_max_agent_attempts: draft.maxAttempts,
  };
}

/**
 * The transition-command editor for one workspace: its checks, transition
 * script and self-repair settings, with test-run and save. Gate Studio mounts
 * it as the detail of every transition-command control; which workspace, and
 * what happens to unsaved edits when the operator navigates away, are the
 * studio's — this reports `dirty` so the studio can ask first (863).
 */
export function WorkspaceGatesPanel({
  workspaceSlug: selectedSlug,
  onDirtyChange,
}: {
  workspaceSlug: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [report, setReport] = useState<GateTestReport | null>(null);
  const activeField = useRef<{ id: number; el: HTMLTextAreaElement } | null>(null);

  const profile = useQuery({
    queryKey: ["orchestration-profile", selectedSlug],
    queryFn: () => api.orchestrationProfile(selectedSlug),
    enabled: Boolean(selectedSlug),
  });

  // Reset per-workspace UI only when the user switches workspaces — NOT on
  // every profile.data change, since a successful save writes its response
  // into this same query's cache and would otherwise clobber "Saved.".
  useEffect(() => {
    setSavedAt(null);
    setReport(null);
    setDraft(null);
    activeField.current = null;
  }, [selectedSlug]);

  useEffect(() => {
    if (profile.data) setDraft(draftFrom(profile.data));
  }, [profile.data]);

  const baseline = useMemo(
    () => (profile.data ? JSON.stringify(payloadOf(draftFrom(profile.data))) : ""),
    [profile.data],
  );
  const dirty = draft !== null && JSON.stringify(payloadOf(draft)) !== baseline;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  const unrunnable = draft ? draft.commands.filter((c) => hasUnbalancedQuotes(c.value)).length : 0;

  const saveGates = useMutation({
    meta: { errorTitle: "Save gates" },
    mutationFn: (next: Draft) => api.updateWorkspaceGates(selectedSlug, payloadOf(next)),
    onSuccess: (updated: OrchestrationProfileView) => {
      qc.setQueryData(["orchestration-profile", selectedSlug], updated);
      setSavedAt(Date.now());
    },
  });

  const testGates = useMutation({
    meta: { errorTitle: "Test gate checks" },
    mutationFn: (commands: string[]) => api.testWorkspaceGates(selectedSlug, { commands }),
    onSuccess: setReport,
  });

  const results = useMemo(
    () => new Map<string, GateTestCommandResult>(report?.results.map((r) => [r.template, r])),
    [report],
  );

  const patch = (next: Partial<Draft>) => {
    setSavedAt(null);
    setDraft((prev) => (prev ? { ...prev, ...next } : prev));
  };

  const canSave = dirty && unrunnable === 0 && !saveGates.isPending;
  const save = useCallback(() => {
    if (draft && canSave) saveGates.mutate(draft);
  }, [draft, canSave, saveGates]);

  const insertPlaceholder = (name: string) => {
    if (!draft) return;
    const token = `{${name}}`;
    const field = activeField.current;
    const target = draft.commands.find((c) => c.id === field?.id);
    if (!field || !target || !field.el.isConnected) {
      // Nothing focused yet: start a new check with the placeholder in it.
      patch({ commands: [...draft.commands, { id: newId(), value: token }] });
      return;
    }
    const { value, caret } = insertAt(
      target.value,
      token,
      field.el.selectionStart ?? target.value.length,
      field.el.selectionEnd ?? target.value.length,
    );
    patch({ commands: draft.commands.map((c) => (c.id === target.id ? { ...c, value } : c)) });
    requestAnimationFrame(() => {
      field.el.focus();
      field.el.setSelectionRange(caret, caret);
    });
  };

  const placeholders = profile.data?.gates_placeholders ?? {};
  const present = new Set(draft?.commands.map((c) => c.value.trim()));
  const suggestions = (profile.data?.gates_suggested_commands ?? []).filter((s) => !present.has(s));
  const runnable = draft ? cleaned(draft.commands) : [];
  const staleReport =
    report !== null && report.results.some((r) => !runnable.includes(r.template));

  return (
    <div
      className="gate-editor"
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          save();
        }
      }}
    >
          {profile.isError ? (
            <div className="gate-load-error" role="alert">
              <p>
                Couldn't load the gate settings for workspace {selectedSlug}:{" "}
                {describeError(profile.error, "the request failed")}
              </p>
              <button type="button" className="btn-secondary" onClick={() => profile.refetch()}>
                Try again
              </button>
            </div>
          ) : !profile.data || !draft ? (
            <div
              className="gate-skeleton"
              aria-busy="true"
              aria-label={`Loading gate settings for ${selectedSlug}`}
            >
              <div className="gate-skeleton-bar gate-skeleton-bar--title" />
              <div className="gate-skeleton-bar" />
              <div className="gate-skeleton-card" />
              <div className="gate-skeleton-card" />
            </div>
          ) : (
            <>
              <GateStatusHeader
                profile={profile.data}
                enabled={draft.enabled}
                onToggle={(enabled) => patch({ enabled })}
                checkCount={runnable.length}
                fixerCount={cleaned(draft.autofixCommands).length}
                agentFallback={draft.agentFallback}
                maxAttempts={draft.maxAttempts}
              />

              <div className={draft.enabled ? "gate-body" : "gate-body gate-body--off"}>
                <section className="gate-card" aria-labelledby="gate-checks-title">
                  <div className="gate-card-head">
                    <h3 id="gate-checks-title">Checks</h3>
                    <span className="gate-card-meta">run in order, stop at the first failure</span>
                  </div>

                  <div className="gate-chips" role="group" aria-label="Insert a placeholder">
                    <span className="gate-chips-label">Insert</span>
                    {Object.keys(placeholders).map((name) => (
                      <button
                        key={name}
                        type="button"
                        className="gate-chip"
                        title={`${PLACEHOLDER_HELP[name] ?? name} — e.g. ${placeholders[name]}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => insertPlaceholder(name)}
                      >
                        {`{${name}}`}
                      </button>
                    ))}
                  </div>

                  <GateCommandList
                    noun="check"
                    commands={draft.commands}
                    onChange={(commands) => patch({ commands })}
                    onFieldFocus={(id, el) => {
                      activeField.current = { id, el };
                    }}
                    newId={newId}
                    placeholders={placeholders}
                    results={staleReport ? undefined : results}
                    testing={testGates.isPending}
                    emptyText="No checks yet — every transition passes straight through. Add one below, or start from a built-in guardrail."
                    inputPlaceholder="e.g. ruff check {workspace_root}"
                  />

                  {suggestions.length > 0 && (
                    <div className="gate-suggestions">
                      <span className="gate-chips-label">Built-in guardrails</span>
                      {suggestions.map((command) => (
                        <button
                          key={command}
                          type="button"
                          className="gate-suggestion"
                          title={command}
                          onClick={() =>
                            patch({ commands: [...draft.commands, { id: newId(), value: command }] })
                          }
                        >
                          + {gateCommandLabel(command)}
                        </button>
                      ))}
                    </div>
                  )}

                  <TestSummary
                    report={report}
                    stale={staleReport}
                    pending={testGates.isPending}
                    canTest={runnable.length > 0}
                    onTest={() => testGates.mutate(runnable)}
                  />
                </section>

                <section className="gate-card" aria-labelledby="gate-script-title">
                  <div className="gate-card-head">
                    <h3 id="gate-script-title">Transition script</h3>
                    <span className="gate-card-meta">optional · runs before the checks</span>
                  </div>
                  <label className="gate-field">
                    <span className="gate-field-label">Path in the workspace repo</span>
                    <input
                      className="studio-input gate-mono"
                      value={draft.transitionScript}
                      placeholder={DEFAULT_SCRIPT}
                      spellCheck={false}
                      onChange={(e) => patch({ transitionScript: e.target.value })}
                    />
                  </label>
                  <ScriptResolution
                    configured={profile.data.gates_transition_script}
                    resolved={profile.data.gates_transition_script_resolved}
                    changed={draft.transitionScript.trim() !== profile.data.gates_transition_script}
                  />
                  <p className="gate-hint">
                    Called with <code>--ticket-id</code>, <code>--transition</code> and{" "}
                    <code>--checkpoints-dir</code>. An edge the script doesn't model (argparse
                    "invalid choice") counts as no gate, not a failure.
                  </p>
                </section>

                <section className="gate-card" aria-labelledby="gate-repair-title">
                  <div className="gate-card-head">
                    <h3 id="gate-repair-title">When a check fails</h3>
                    <span className="gate-card-meta">self-repair before it reaches you</span>
                  </div>
                  <div className="gate-field-label">1 · Fixers — run best-effort, then the checks re-run</div>
                  <GateCommandList
                    noun="fixer"
                    commands={draft.autofixCommands}
                    onChange={(autofixCommands) => patch({ autofixCommands })}
                    newId={newId}
                    placeholders={placeholders}
                    emptyText="No fixers. A failed check goes straight to the agent retry below."
                    inputPlaceholder="e.g. ruff format {workspace_root}"
                  />
                  <div className="gate-field-label gate-field-label--spaced">
                    2 · Agent retry — the stage's agent gets the failure and another pass
                  </div>
                  <div className="gate-inline">
                    <label className="gate-switch-label">
                      <input
                        type="checkbox"
                        className="gate-switch"
                        role="switch"
                        checked={draft.agentFallback}
                        onChange={(e) => patch({ agentFallback: e.target.checked })}
                      />
                      Retry with the agent
                    </label>
                    <label className="gate-attempts">
                      up to
                      <input
                        type="number"
                        className="studio-input gate-attempts-input"
                        min={0}
                        max={10}
                        value={draft.maxAttempts}
                        disabled={!draft.agentFallback}
                        onChange={(e) =>
                          patch({
                            maxAttempts: Math.min(10, Math.max(0, Number(e.target.value) || 0)),
                          })
                        }
                      />
                      times
                    </label>
                  </div>
                  <p className="gate-hint">3 · Still failing? The ticket blocks and lands in your inbox.</p>
                </section>
              </div>

              <div className="gate-savebar">
                <span className="gate-savebar-status" aria-live="polite">
                  {saveGates.isPending
                    ? "Saving…"
                    : unrunnable > 0
                      ? `Fix ${unrunnable} check${unrunnable > 1 ? "s" : ""} with an unclosed quote to save.`
                      : dirty
                        ? "Unsaved changes"
                        : savedAt
                          ? "Saved."
                          : "All changes saved"}
                </span>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={!dirty || saveGates.isPending}
                  onClick={() => {
                    if (profile.data) setDraft(draftFrom(profile.data));
                  }}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn-primary btn-cta"
                  disabled={!canSave}
                  title="Save (Ctrl/⌘+S)"
                  onClick={save}
                >
                  {saveGates.isPending ? "Saving…" : "Save gates"}
                </button>
              </div>
            </>
          )}
    </div>
  );
}

function ScriptResolution({
  configured,
  resolved,
  changed,
}: {
  configured: string;
  resolved: string;
  changed: boolean;
}) {
  if (changed) {
    return <p className="gate-resolve">Save to check whether this path exists.</p>;
  }
  if (resolved) {
    const fallback = configured && resolved !== configured;
    return (
      <p className="gate-resolve gate-resolve--ok">
        ✓ Runs <code>{resolved}</code>
        {fallback && ` — ${configured} wasn't found, so the default path is used`}
      </p>
    );
  }
  return (
    <p className="gate-resolve gate-resolve--none">
      No script runs — {configured ? <code>{configured}</code> : "the default path"} doesn't exist
      in this workspace.
    </p>
  );
}

function TestSummary({
  report,
  stale,
  pending,
  canTest,
  onTest,
}: {
  report: GateTestReport | null;
  stale: boolean;
  pending: boolean;
  canTest: boolean;
  onTest: () => void;
}) {
  const counts = report?.results.reduce<Record<string, number>>((acc, r) => {
    acc[r.outcome] = (acc[r.outcome] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <div className="gate-test">
      <button
        type="button"
        className="btn-secondary"
        disabled={!canTest || pending}
        onClick={onTest}
      >
        {pending ? "Testing checks…" : "Test checks"}
      </button>
      <span className="gate-test-status" aria-live="polite">
        {pending
          ? "Running each check against the shared checkout — this can take a minute."
          : !report
            ? "Runs the checks above, unsaved edits included, against the shared checkout with sample ticket values. Nothing is saved."
            : stale
              ? "Checks changed since the last test — run it again."
              : [
                  counts?.passed && `${counts.passed} passed`,
                  counts?.failed && `${counts.failed} failed`,
                  counts?.unavailable && `${counts.unavailable} couldn't run`,
                ]
                  .filter(Boolean)
                  .join(" · ") + ` — in ${report.repo_root}`}
      </span>
    </div>
  );
}
