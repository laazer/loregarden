/**
 * What the queue does with a run's work once it finishes.
 *
 * Rendered in the Controls tab, once per workspace. With several workspaces
 * each is a one-line summary of the steps it runs, and only the one you open
 * shows its switches — five workspaces drawn in full were ~3000px of identical
 * cards in a 300px rail. The four publish steps are drawn as a chain
 * because that is what they are on the server: it stops at the first one that
 * is off, so a step whose predecessor is off is shown disabled rather than
 * letting someone tick "open PR" with "push" off and get silence.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { GitAutomationView } from "../api/types";
import { describeError } from "../state/toastStore";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import "./QueueGitAutomation.css";

interface ToggleDef {
  key: keyof GitAutomationView;
  label: string;
  /** The word for this step in a collapsed workspace's summary line. */
  short: string;
  hint: string;
  /** The switch this one is meaningless without. */
  requires?: keyof GitAutomationView;
}

const TOGGLES: ToggleDef[] = [
  {
    key: "worktree",
    short: "Worktree",
    label: "Run in a worktree",
    hint: "Each run gets its own checkout, so parallel runs stop sharing a working tree.",
  },
  {
    key: "commit",
    short: "Commit",
    label: "Commit",
    hint: "Commit the run's work to its branch when it finishes.",
  },
  {
    key: "push",
    short: "Push",
    label: "Push",
    hint: "Push the branch to origin.",
    requires: "commit",
  },
  {
    key: "open_pr",
    short: "PR",
    label: "Open a pull request",
    hint: "Open a PR against the base branch, or reuse one that is already open.",
    requires: "push",
  },
  {
    key: "auto_merge",
    short: "Merge",
    label: "Auto-merge",
    // Not gated on the PR: with a PR open this enables GitHub auto-merge, and
    // without one it merges the run's worktree branch into the base directly.
    // Either way it needs commits to merge.
    hint: "Land the work automatically — via the PR when there is one, locally otherwise.",
    requires: "commit",
  },
  {
    key: "auto_resolve_conflicts",
    short: "Resolve",
    label: "Auto-resolve conflicts",
    hint: "On a conflict, hand the conflicted files to an agent instead of blocking.",
    requires: "auto_merge",
  },
];

/** Every workspace's settings, one open at a time. */
export function QueueGitAutomationList({
  workspaces,
}: {
  workspaces: { id: string; slug: string; name: string }[];
}) {
  const [openSlug, setOpenSlug] = useState<string | null>(null);

  if (!workspaces.length) {
    return (
      <div className="queue-git-automation">
        <div className="queue-rail-heading">On run completion</div>
        <p className="queue-rail-empty">
          No workspaces yet. Add one, and its commit, push and merge settings appear here.
        </p>
      </div>
    );
  }

  // One workspace has nothing to choose between: show it open.
  if (workspaces.length === 1) {
    return (
      <QueueGitAutomation workspaceSlug={workspaces[0].slug} workspaceName={workspaces[0].name} />
    );
  }

  return (
    <div className="queue-git-automation">
      <div className="queue-rail-heading">On run completion</div>
      <div className="queue-git-workspaces">
        {workspaces.map((ws) => (
          <QueueGitAutomation
            key={ws.id}
            workspaceSlug={ws.slug}
            workspaceName={ws.name}
            expanded={openSlug === ws.slug}
            onToggleExpanded={() => setOpenSlug((cur) => (cur === ws.slug ? null : ws.slug))}
          />
        ))}
      </div>
    </div>
  );
}

export function QueueGitAutomation({
  workspaceSlug,
  workspaceName,
  expanded = true,
  onToggleExpanded,
}: {
  workspaceSlug: string;
  /** Shown when several workspaces share the Controls rail. */
  workspaceName?: string;
  /** Collapsible mode: given, the panel is a summary row that opens on click. */
  expanded?: boolean;
  onToggleExpanded?: () => void;
}) {
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

  if (onToggleExpanded) {
    const bodyId = `queue-git-${workspaceSlug}`;
    return (
      <div className={`queue-git-workspace${expanded ? " is-open" : ""}`}>
        <Button
          variant="plain"
          className="queue-git-summary"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={onToggleExpanded}
        >
          <span className="queue-git-summary-name">{workspaceName ?? workspaceSlug}</span>
          <span className="queue-git-summary-steps">
            {config.isLoading
              ? "Loading…"
              : config.isError || !current
                ? "Could not load"
                : summarize(current)}
          </span>
          <span className="queue-git-summary-chevron" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        </Button>
        {expanded ? (
          <div id={bodyId} className="queue-git-workspace-body">
            <GitAutomationBody
              config={config}
              current={current}
              save={save}
              workspaceSlug={workspaceSlug}
            />
          </div>
        ) : null}
      </div>
    );
  }

  const heading = workspaceName
    ? `On run completion · ${workspaceName}`
    : "On run completion";

  return (
    <div className="queue-git-automation">
      <div className="queue-rail-heading">{heading}</div>
      <GitAutomationBody
        config={config}
        current={current}
        save={save}
        workspaceSlug={workspaceSlug}
      />
    </div>
  );
}

function GitAutomationBody({
  config,
  current,
  save,
  workspaceSlug,
}: {
  config: { isLoading: boolean; isError: boolean };
  current: GitAutomationView | undefined;
  save: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    mutate: (body: GitAutomationView) => void;
  };
  workspaceSlug: string;
}) {
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
    <div className="queue-git-body">
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

      <BaseBranchField
        key={workspaceSlug}
        value={current.base_branch}
        disabled={save.isPending}
        onCommit={(base_branch) => save.mutate({ ...current, base_branch })}
      />

      {current.auto_resolve_conflicts ? (
        <label className="queue-git-field">
          <span className="queue-git-field-label">Resolution attempts</span>
          <Input
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
        <p className="queue-git-error">
          Could not save: {describeError(save.error, "the workspace profile rejected the change")}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Saves on blur or Enter, not per keystroke: typing "release" used to send
 * seven saves, each of a branch name nobody meant.
 */
function BaseBranchField({
  value,
  disabled,
  onCommit,
}: {
  value: string;
  disabled: boolean;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const commit = () => {
    const next = draft.trim();
    if (!next) {
      setDraft(value);
      return;
    }
    if (next !== value) onCommit(next);
  };

  return (
    <label className="queue-git-field">
      <span className="queue-git-field-label">Base branch</span>
      <Input
        className="queue-git-field-input"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") setDraft(value);
        }}
      />
    </label>
  );
}

/** "Worktree · Commit · Push → main", or what happens when nothing is on. */
function summarize(config: GitAutomationView): string {
  const on = TOGGLES.filter((toggle) => config[toggle.key]).map((toggle) => toggle.short);
  if (!on.length) return "Off — work stays in the run's checkout";
  return config.commit
    ? `${on.join(" · ")} → ${config.base_branch}`
    : `${on.join(" · ")} · nothing committed`;
}

function labelFor(key: keyof GitAutomationView): string {
  return TOGGLES.find((toggle) => toggle.key === key)?.label ?? key;
}
