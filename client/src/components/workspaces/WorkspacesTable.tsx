/**
 * The workspaces as a table: "which of my repositories needs something from
 * loregarden, and what fixes it?"
 *
 * One row per workspace, one action per row (its name opens its dashboard),
 * and a Needs column where every problem carries the button that fixes it.
 * The fix for a missing or stale block opens a pull request in that repository
 * (`WorkspaceFixDialog` says how); a missing repository is created where a
 * folder can hold one. Setup in place, gate presets, launch templates and
 * archiving are in the row's ⋯ menu.
 *
 * Each row's integration check is the same query, and cache, the setup panel
 * reads, so opening Set up… does not re-run the installers.
 */

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type { WorkspaceIntegration, WorkspaceTemplates } from "../../api/localInstancesTypes";
import type { WorkspaceSummary } from "../../api/types";
import { INTEGRATION_KEY, WORKSPACE_TEMPLATES_KEY } from "../../hooks/useLocalInstances";
import { useIndexSort, type IndexColumn } from "../../lib/indexSort";
import { workspaceDashboardPath } from "../../lib/appNavigation";
import { NotThisTarget } from "../../lib/agentActions/registry";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { gatesCell, workspaceNeeds, type WorkspaceNeed } from "../../lib/workspaceNeeds";
import { describeError, pushToast } from "../../state/toastStore";
import { OverflowMenu, OverflowMenuItem } from "../OverflowMenu";
import { SortableHead } from "../studio/StudioIndex";
import { Button } from "../ui/Button";
import { WorkspaceFixDialog, type PendingFix } from "./WorkspaceFixDialog";
import { WorkspaceSetupDialog } from "./WorkspaceSetupDialog";

const CHECK_STALE_MS = 60_000;

type SortKey = "name" | "tickets" | "blocked" | "needs";

const COLUMNS: IndexColumn<SortKey>[] = [
  { key: "name", label: "Workspace", numeric: false, first: "ascending" },
  { key: "tickets", label: "Tickets", numeric: true, first: "descending" },
  { key: "blocked", label: "Blocked", numeric: true, first: "descending" },
  { key: "needs", label: "Needs", numeric: false, first: "descending" },
];

interface Row {
  workspace: WorkspaceTemplates;
  summary: WorkspaceSummary | undefined;
  integration: { data: WorkspaceIntegration | undefined; isPending: boolean; error: unknown; refetch: () => unknown };
  needs: WorkspaceNeed[];
}

const COMPARE: Record<SortKey, (a: Row, b: Row) => number> = {
  name: (a, b) => a.workspace.name.localeCompare(b.workspace.name),
  tickets: (a, b) => (a.summary?.ticket_count ?? 0) - (b.summary?.ticket_count ?? 0),
  blocked: (a, b) => (a.summary?.blocked_count ?? 0) - (b.summary?.blocked_count ?? 0),
  needs: (a, b) => a.needs.length - b.needs.length,
};

/** `https://github.com/o/r/pull/9` → `o/r#9`, the way a PR is named in prose. */
function prName(url: string): string {
  const match = /github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/.exec(url);
  return match ? `${match[1]}#${match[2]}` : url;
}

interface Props {
  workspaces: WorkspaceTemplates[];
  summaries: Map<string, WorkspaceSummary>;
  onArchive: (workspace: WorkspaceTemplates, archive: boolean) => void;
  archivingSlug: string | null;
  caption: string;
}

export function WorkspacesTable({ workspaces, summaries, onArchive, archivingSlug, caption }: Props) {
  const queryClient = useQueryClient();
  const { sort, onSort } = useIndexSort<SortKey>({ key: "needs", dir: "descending" });
  const [pending, setPending] = useState<PendingFix | null>(null);
  const [settingUp, setSettingUp] = useState<WorkspaceTemplates | null>(null);
  /** PRs opened from this page, by `slug:installer`: the need shows its link instead of its button. */
  const [opened, setOpened] = useState<ReadonlyMap<string, string>>(new Map());

  const checks = useQueries({
    queries: workspaces.map((workspace) => ({
      queryKey: [...INTEGRATION_KEY, workspace.slug],
      queryFn: () => localInstancesApi.integration(workspace.slug),
      staleTime: CHECK_STALE_MS,
    })),
  });

  const fix = useMutation({
    // The dialog shows the reason inline and stays open; a toast beside it would say it twice.
    meta: { suppressErrorToast: true },
    mutationFn: async (target: PendingFix) =>
      target.fix.kind === "pull_request"
        ? { kind: "pr" as const, result: await localInstancesApi.installPullRequest(target.slug, target.fix.installer) }
        : { kind: "repo" as const, result: await api.createWorkspaceRepository(target.slug) },
    onSuccess: (done, target) => {
      setPending(null);
      if (done.kind === "pr") {
        const { outcome, detail } = done.result;
        const targetFix = target.fix;
        if (outcome === "opened" && targetFix.kind === "pull_request") {
          setOpened((previous) => new Map(previous).set(`${target.slug}:${targetFix.installer}`, detail));
        }
        pushToast(
          outcome === "opened"
            ? { tone: "success", title: `Opened ${prName(detail)}`, message: detail, duration: 0 }
            : { tone: "info", title: `Nothing to change in ${target.name}`, message: detail },
        );
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      void queryClient.invalidateQueries({ queryKey: [...INTEGRATION_KEY, target.slug] });
      void queryClient.invalidateQueries({ queryKey: WORKSPACE_TEMPLATES_KEY });
      pushToast(
        done.result.follow_up
          ? { tone: "warning", title: `Created ${target.name}'s repository`, message: done.result.follow_up }
          : { tone: "success", title: `Created ${target.name}'s repository`, message: done.result.repo_root },
      );
    },
  });

  const rows: Row[] = workspaces.map((workspace, index) => {
    const summary = summaries.get(workspace.slug);
    const check = checks[index];
    return {
      workspace,
      summary,
      integration: check,
      needs: check.data || summary ? workspaceNeeds(summary, check.data) : [],
    };
  });
  const sign = sort.dir === "ascending" ? 1 : -1;
  const sorted = [...rows].sort(
    (a, b) =>
      sign * COMPARE[sort.key](a, b) ||
      (b.summary?.ticket_count ?? 0) - (a.summary?.ticket_count ?? 0) ||
      a.workspace.name.localeCompare(b.workspace.name),
  );

  // Formerly one per card; one here, answering for any workspace on the table.
  useAgentAction("workspace.create_repository", async ({ workspace_slug }) => {
    const row = rows.find((candidate) => candidate.workspace.slug === workspace_slug);
    // The active and archived tables both answer; the one without it passes it on.
    if (!row) throw new NotThisTarget(`no workspace named ${workspace_slug} is on this table`);
    const need = row.needs.find((candidate) => candidate.fix?.kind === "create_repository");
    if (!need?.fix) {
      throw new Error(
        `${workspace_slug}'s repository is ${row.summary?.repo_state ?? "unknown"}; there is nothing to create`,
      );
    }
    const done = await fix.mutateAsync({
      slug: row.workspace.slug,
      name: row.workspace.name,
      repoRoot: row.workspace.repo_root,
      fix: need.fix,
    });
    const created = done.kind === "repo" ? done.result : null;
    return { workspace_slug, repo_root: created?.repo_root ?? "", follow_up: created?.follow_up ?? "" };
  });

  const needsCell = (row: Row) => {
    if (row.integration.isPending && !row.summary) return <span className="studio-index-muted">checking…</span>;
    if (row.integration.error) {
      return (
        <span className="workspaces-need">
          <span className="workspaces-need-label">
            Check failed: {describeError(row.integration.error, "the installers did not answer")}
          </span>
          <Button variant="secondary" compact onClick={() => void row.integration.refetch()}>
            Check again
          </Button>
        </span>
      );
    }
    if (row.integration.isPending && row.needs.length === 0) return <span className="studio-index-muted">checking…</span>;
    if (row.needs.length === 0) return <span className="studio-index-muted">—</span>;
    return (
      <ul className="workspaces-needs">
        {row.needs.map((need) => {
          const needFix = need.fix;
          const prUrl =
            needFix?.kind === "pull_request" ? opened.get(`${row.workspace.slug}:${needFix.installer}`) : undefined;
          return (
            <li key={need.id} className="workspaces-need">
              <span className="workspaces-need-label">{need.label}</span>
              {prUrl ? (
                <a className="workspaces-need-link" href={prUrl} target="_blank" rel="noreferrer">
                  {prName(prUrl)} opened ↗
                </a>
              ) : needFix ? (
                <Button
                  variant="secondary"
                  compact
                  aria-label={`${needFix.label.replace(/…$/, "")} in ${row.workspace.name}…`}
                  disabled={fix.isPending}
                  onClick={() => {
                    fix.reset();
                    setPending({
                      slug: row.workspace.slug,
                      name: row.workspace.name,
                      repoRoot: row.workspace.repo_root,
                      fix: needFix,
                    });
                  }}
                >
                  {needFix.label}
                </Button>
              ) : (
                <span className="workspaces-need-explain">{need.explain}</span>
              )}
            </li>
          );
        })}
      </ul>
    );
  };

  return (
    <>
      <table className="studio-index-table workspaces-table">
        <caption className="studio-index-sr-only">{caption}</caption>
        <SortableHead columns={COLUMNS} sort={sort} onSort={onSort} trailing="Actions" />
        <tbody>
          {sorted.map((row) => {
            const gates = gatesCell(row.integration.data);
            const archived = Boolean(row.summary?.archived_at);
            return (
              <tr key={row.workspace.slug}>
                <th scope="row">
                  <Link className="studio-index-name" to={workspaceDashboardPath(row.workspace.slug)}>
                    {row.workspace.name}
                  </Link>
                  <span className="studio-index-slug">{row.workspace.repo_root}</span>
                  {gates ? (
                    <span className="workspaces-gates">
                      Gates: {gates.word}
                      {/* With no repository, the Needs column already says why. */}
                      {gates.detail && row.summary?.repo_state === "repository" ? ` (${gates.detail})` : ""}
                    </span>
                  ) : null}
                </th>
                <td className="num" data-label="Tickets">
                  {row.summary ? row.summary.ticket_count.toLocaleString() : "—"}
                </td>
                <td className="num" data-label="Blocked">
                  {row.summary ? row.summary.blocked_count.toLocaleString() : "—"}
                </td>
                <td data-label="Needs">{needsCell(row)}</td>
                <td className="workspaces-row-menu">
                  <OverflowMenu label={`${row.workspace.name} actions`} align="right">
                    <OverflowMenuItem onSelect={() => setSettingUp(row.workspace)}>Set up…</OverflowMenuItem>
                    {row.summary ? (
                      <OverflowMenuItem
                        disabled={archivingSlug === row.workspace.slug}
                        danger={!archived}
                        onSelect={() => onArchive(row.workspace, !archived)}
                      >
                        {archived ? `Restore ${row.workspace.name}` : `Archive ${row.workspace.name}…`}
                      </OverflowMenuItem>
                    ) : null}
                  </OverflowMenu>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <WorkspaceFixDialog
        pending={pending}
        inFlight={fix.isPending}
        error={fix.error}
        onClose={() => setPending(null)}
        onConfirm={(target) => fix.mutate(target)}
      />
      <WorkspaceSetupDialog workspace={settingUp} onClose={() => setSettingUp(null)} />
    </>
  );
}
