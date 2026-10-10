import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { GateTestCommandResult } from "../../api/gateTypes";
import { presetCommands, saveGateSelection } from "../../lib/gateSelection";
import { studioResourcePath } from "../../lib/appNavigation";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { GatePresetPicker } from "./GatePresetPicker";

/** Outcomes a transition would block on. */
const BLOCKING = new Set(["failed", "unavailable"]);

interface WorkspaceGatePresetsPanelProps {
  slug: string;
  name: string;
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
export function WorkspaceGatePresetsPanel({ slug, name }: WorkspaceGatePresetsPanelProps) {
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
  const configured = useMemo(
    () => new Set((profile.data?.gates_commands ?? []).filter((command) => offered.has(command))),
    [profile.data, offered],
  );
  const [selected, setSelected] = useState<ReadonlySet<string>>(configured);
  const [failures, setFailures] = useState<GateTestCommandResult[]>([]);
  useEffect(() => {
    setSelected(configured);
    setFailures([]);
  }, [configured]);

  const add = [...selected].filter((command) => !configured.has(command));
  const remove = [...configured].filter((command) => !selected.has(command));

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
          <GatePresetPicker
            presets={presets.data}
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
