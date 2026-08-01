/**
 * What the queue does with a run's work once it finishes.
 *
 * Rendered in the Controls tab. The four publish steps are drawn as a chain
 * because that is what they are on the server: it stops at the first one that
 * is off, so a step whose predecessor is off is shown disabled rather than
 * letting someone tick "open PR" with "push" off and get silence.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { GitAutomationView } from "../api/types";
import "./QueueGitAutomation.css";

interface ToggleDef {
  key: keyof GitAutomationView;
  label: string;
  hint: string;
  /** The switch this one is meaningless without. */
  requires?: keyof GitAutomationView;
}

const TOGGLES: ToggleDef[] = [
  {
    key: "worktree",
    label: "Run in a worktree",
    hint: "Each run gets its own checkout, so parallel runs stop sharing a working tree.",
  },
  {
    key: "commit",
    label: "Commit",
    hint: "Commit the run's work to its branch when it finishes.",
  },
  {
    key: "push",
    label: "Push",
    hint: "Push the branch to origin.",
    requires: "commit",
  },
  {
    key: "open_pr",
    label: "Open a pull request",
    hint: "Open a PR against the base branch, or reuse one that is already open.",
    requires: "push",
  },
  {
    key: "auto_merge",
    label: "Auto-merge",
    // Not gated on the PR: with a PR open this enables GitHub auto-merge, and
    // without one it merges the run's worktree branch into the base directly.
    // Either way it needs commits to merge.
    hint: "Land the work automatically — via the PR when there is one, locally otherwise.",
    requires: "commit",
  },
  {
    key: "auto_resolve_conflicts",
    label: "Auto-resolve conflicts",
    hint: "On a conflict, hand the conflicted files to an agent instead of blocking.",
    requires: "auto_merge",
  },
];

export function QueueGitAutomation({ workspaceSlug }: { workspaceSlug: string }) {
  const qc = useQueryClient();

  const config = useQuery({
    queryKey: ["git-automation", workspaceSlug],
    queryFn: () => api.gitAutomation(workspaceSlug),
    enabled: Boolean(workspaceSlug),
  });

  const save = useMutation({
    mutationFn: (body: GitAutomationView) => api.updateGitAutomation(workspaceSlug, body),
    onSuccess: (data) => {
      qc.setQueryData(["git-automation", workspaceSlug], data);
    },
  });

  const current = save.isPending ? save.variables : config.data;

  if (config.isLoading) {
    return <p className="queue-rail-empty">Loading automation settings…</p>;
  }

  if (config.isError || !current) {
    return <p className="queue-rail-empty">Could not load automation settings.</p>;
  }

  const setFlag = (key: keyof GitAutomationView, value: boolean) => {
    const next: GitAutomationView = { ...current, [key]: value };

    // Turning a step off turns off everything downstream of it, because the
    // server would skip those anyway — leaving them ticked would claim the
    // queue does something it does not.
    if (!value) {
      let cleared = true;
      while (cleared) {
        cleared = false;
        for (const toggle of TOGGLES) {
          if (toggle.requires && !next[toggle.requires] && next[toggle.key]) {
            (next[toggle.key] as boolean) = false;
            cleared = true;
          }
        }
      }
    }

    save.mutate(next);
  };

  return (
    <div className="queue-git-automation">
      <div className="queue-rail-heading">On run completion</div>

      <div className="queue-git-toggles">
        {TOGGLES.map((toggle) => {
          const blocked = Boolean(toggle.requires && !current[toggle.requires]);
          return (
            <label
              key={toggle.key}
              className={`queue-git-toggle${blocked ? " is-blocked" : ""}`}
            >
              <input
                type="checkbox"
                checked={Boolean(current[toggle.key])}
                disabled={blocked || save.isPending}
                onChange={(event) => setFlag(toggle.key, event.target.checked)}
              />
              <span className="queue-git-toggle-copy">
                <span className="queue-git-toggle-label">{toggle.label}</span>
                <span className="queue-git-toggle-hint">
                  {blocked ? `Needs "${labelFor(toggle.requires!)}" first.` : toggle.hint}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      <label className="queue-git-field">
        <span className="queue-git-field-label">Base branch</span>
        <input
          className="queue-git-field-input"
          value={current.base_branch}
          disabled={save.isPending}
          onChange={(event) => save.mutate({ ...current, base_branch: event.target.value })}
        />
      </label>

      {current.auto_resolve_conflicts ? (
        <label className="queue-git-field">
          <span className="queue-git-field-label">Resolution attempts</span>
          <input
            className="queue-git-field-input"
            type="number"
            min={1}
            max={10}
            value={current.max_conflict_resolve_attempts}
            disabled={save.isPending}
            onChange={(event) =>
              save.mutate({
                ...current,
                max_conflict_resolve_attempts: Number(event.target.value) || 1,
              })
            }
          />
        </label>
      ) : null}

      {save.isError ? (
        <p className="queue-git-error">Could not save: {String(save.error)}</p>
      ) : null}
    </div>
  );
}

function labelFor(key: keyof GitAutomationView): string {
  return TOGGLES.find((toggle) => toggle.key === key)?.label ?? key;
}
