import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { GateTestCommandResult } from "../../api/gateTypes";
import {
  classifyGateCommands,
  guardrailScript,
  presetCommands,
  runningPresets,
  saveGateSelection,
  type RunningGates,
} from "../../lib/gateSelection";
import { studioResourcePath } from "../../lib/appNavigation";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { GatePresetPicker } from "./GatePresetPicker";

/** Outcomes a transition would block on. */
const BLOCKING = new Set(["failed", "unavailable"]);

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function runningSummary(running: RunningGates, name: string): string {
  const total = running.presets.length + running.guardrails.length + running.custom.length;
  if (total === 0) return "No checks run on stage transitions yet. Tick toolchain checks below to add some.";
  const parts = [
    running.guardrails.length ? plural(running.guardrails.length, "loregarden guardrail", "loregarden guardrails") : "",
    running.custom.length ? `${running.custom.length} of ${name}'s own` : "",
    running.presets.length ? `${running.presets.length} ticked below` : "",
  ].filter(Boolean);
  return `${plural(total, "check runs", "checks run")} on every stage transition: ${parts.join(", ")}.`;
}

/**
 * What runs today that the picker does not offer. Without it a workspace whose
 * gates are all its own (lore-eden: 11) or spelled differently from a preset
 * (loregarden's `bash -c "cd server && uv run ruff check ."`) read as having
 * none, because the picker can only tick commands it offers.
 */
function RunningGatesList({ running, name }: { running: RunningGates; name: string }) {
  return (
    <div className="gate-running">
      <p className="instances-meta">{runningSummary(running, name)}</p>
      {running.guardrails.length > 0 && (
        <>
          <h4 className="gate-running-title">loregarden guardrails</h4>
          <ul className="gate-running-list">
            {running.guardrails.map((command) => (
              <li key={command}>
                <code>{guardrailScript(command)}</code>
              </li>
            ))}
          </ul>
        </>
      )}
      {running.custom.length > 0 && (
        <>
          <h4 className="gate-running-title">{name}&rsquo;s own checks, edited in Studio</h4>
          <ul className="gate-running-list">
            {running.custom.map((command) => (
              <li key={command}>
                <code>{command.replaceAll("{workspace_root}/", "").replaceAll("{workspace_root}", ".")}</code>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

interface WorkspaceGatePresetsPanelProps {
  slug: string;
  name: string;
  /** Told whether ticks are waiting to be saved, so a closing dialog can ask first. */
  onDirtyChange?: (dirty: boolean) => void;
}

const sameSet = (a: ReadonlySet<string>, b: ReadonlySet<string>) => a.size === b.size && [...a].every((x) => b.has(x));

/**
 * Which of a workspace's toolchain checks run on every stage transition.
 *
 * Answers "what does a transition check here beyond loregarden's guardrails?"
 * and changes it. A command being added is run against the current checkout
 * first: one that fails today blocks every transition until someone fixes the
 * code — a repo with lint debt and a newly ticked `ruff check` is exactly that —
 * so its output is shown and saving it anyway is a deliberate second click.
 * Commands not offered here (guardrails, anything hand-written in Studio) are
 * never touched.
 */
export function WorkspaceGatePresetsPanel({ slug, name, onDirtyChange }: WorkspaceGatePresetsPanelProps) {
  const queryClient = useQueryClient();
  const presets = useQuery({
    queryKey: ["gate-presets", "workspace", slug],
    queryFn: () => api.gatePresetsForWorkspace(slug),
  });
  const profile = useQuery({
    queryKey: ["orchestration-profile", slug],
    queryFn: () => api.orchestrationProfile(slug),
  });

  const offered = useMemo(() => presetCommands(presets.data), [presets.data]);
  // Preset → the configured command that already does it, under any spelling.
  const runningAs = useMemo(() => runningPresets(presets.data), [presets.data]);
  const running = useMemo(
    () => classifyGateCommands(profile.data?.gates_commands ?? [], new Set([...offered, ...runningAs.values()])),
    [profile.data, offered, runningAs],
  );
  const configured = useMemo(() => new Set(runningAs.keys()), [runningAs]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(configured);
  const [failures, setFailures] = useState<GateTestCommandResult[]>([]);
  useEffect(() => {
    setSelected(configured);
    setFailures([]);
  }, [configured]);

  const add = [...selected].filter((command) => !configured.has(command));
  // Unticking removes what actually runs, which may be spelled unlike the preset.
  const remove = [...configured]
    .filter((command) => !selected.has(command))
    .map((command) => runningAs.get(command) ?? command);
  const dirty = add.length > 0 || remove.length > 0;
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);

  const save = useMutation({
    meta: { suppressErrorToast: true },
    mutationFn: async ({ force }: { force: boolean }) => {
      if (!force && add.length > 0) {
        const report = await api.testWorkspaceGates(slug, { commands: add });
        const blocking = report.results.filter((result) => BLOCKING.has(result.outcome));
        if (blocking.length > 0) return { saved: false, blocking };
      }
      await saveGateSelection(slug, add, remove);
      return { saved: true, blocking: [] };
    },
    onSuccess: ({ saved, blocking }) => {
      setFailures(blocking);
      if (!saved) return;
      void queryClient.invalidateQueries({ queryKey: ["orchestration-profile", slug] });
      // `running_as` is worked out from the profile, so it changed too.
      void queryClient.invalidateQueries({ queryKey: ["gate-presets", "workspace", slug] });
      pushToast({ tone: "success", title: `Saved ${name}'s toolchain gates` });
    },
    onError: (error) => toastActionFailed(`Save ${name}'s toolchain gates`, error),
  });

  const failed = presets.error ?? profile.error;

  return (
    <section className="instances-integration" aria-labelledby={`gate-presets-${slug}`}>
      <header className="instances-section-head">
        <h3 id={`gate-presets-${slug}`}>{name} toolchain gates</h3>
        <Link className="btn-secondary" to={studioResourcePath("gates", slug)}>
          All gates in Studio
        </Link>
      </header>
      <p className="instances-meta">
        Checks each stage transition runs on the agent's checkout, each within 300 seconds — beside loregarden's own
        guardrails, which this leaves alone.
      </p>
      {failed ? (
        <p className="instances-error" role="alert">
          Could not load {name}'s gates: {describeError(failed, "the request failed")}.
        </p>
      ) : !presets.data || !profile.data ? (
        <div className="local-instances-skeleton" aria-label="Loading toolchain gates" />
      ) : (
        <>
          <RunningGatesList running={running} name={name} />
          <GatePresetPicker
            presets={presets.data}
            runningAs={runningAs}
            selected={selected}
            disabled={save.isPending}
            idPrefix={`gates-${slug}`}
            onToggle={(command, on) => {
              setFailures([]);
              setSelected((current) => {
                const next = new Set(current);
                if (on) next.add(command);
                else next.delete(command);
                return next;
              });
            }}
          />
          {failures.length > 0 && (
            <div className="instances-error" role="alert">
              <p>
                {failures.length === 1 ? "This check fails" : `${failures.length} checks fail`} on {name}'s code as it
                is now. Saved, every stage transition will block on {failures.length === 1 ? "it" : "them"} until the
                code passes.
              </p>
              <ul>
                {failures.map((result) => (
                  <li key={result.template}>
                    <code>{result.command}</code> — {result.outcome}
                    {result.message ? `: ${result.message}` : ""}
                    {(result.stderr || result.stdout) && (
                      <pre className="gate-preset-output">{(result.stderr || result.stdout).trim().split("\n").slice(-8).join("\n")}</pre>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="instances-setup-actions">
            <span className="instances-meta" aria-live="polite">
              {save.isPending
                ? add.length > 0 && !save.variables?.force
                  ? `Running ${add.length} new ${add.length === 1 ? "check" : "checks"} against the current code…`
                  : "Saving…"
                : add.length + remove.length === 0
                  ? "No changes."
                  : `${add.length} to add, ${remove.length} to remove.`}
            </span>
            <span>
              {failures.length > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={save.isPending}
                  onClick={() => save.mutate({ force: true })}
                >
                  Save anyway
                </button>
              )}{" "}
              <button
                type="button"
                className="btn-primary"
                disabled={save.isPending || sameSet(selected, configured)}
                aria-busy={save.isPending}
                onClick={() => {
                  if (!save.isPending) save.mutate({ force: false });
                }}
              >
                {save.isPending ? (add.length > 0 ? "Checking…" : "Saving…") : add.length > 0 ? "Check and save" : "Save"}
              </button>
            </span>
          </div>
        </>
      )}
    </section>
  );
}
