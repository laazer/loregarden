import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { DiffArtifact } from "../api/client";
import {
  fetchBranchDiffFile,
  fetchBranchDiffManifest,
  type BranchDiffMode,
  type BranchTriageEntry,
} from "../lib/branchTriageApi";
import { BranchDiffCompareMenu } from "./BranchDiffCompareMenu";
import { BranchTriageCurrentTag } from "./BranchTriageCurrentTag";
import { InlineCodeDiffReview } from "./InlineCodeDiffReview";
import "./BranchTriagePanel.css";

function defaultDiffMode(entry: BranchTriageEntry | undefined, baseBranch: string): BranchDiffMode {
  const options = entry?.diff_options ?? [];
  if (options.length) return options[0].mode;
  return "base";
}

export function BranchTriageDiffPanel({
  workspaceSlug,
  branch,
  baseBranch,
  branchEntry,
}: {
  workspaceSlug: string;
  branch: string | null;
  baseBranch: string;
  branchEntry?: BranchTriageEntry;
}) {
  const diffOptions = useMemo(
    () =>
      branchEntry?.diff_options?.length
        ? branchEntry.diff_options
        : [{ mode: "base" as const, label: `vs ${baseBranch}`, ref: baseBranch }],
    [branchEntry?.diff_options, baseBranch],
  );
  const [diffMode, setDiffMode] = useState<BranchDiffMode>(() =>
    defaultDiffMode(branchEntry, baseBranch),
  );

  useEffect(() => {
    setDiffMode(defaultDiffMode(branchEntry, baseBranch));
  }, [branch, baseBranch]);

  useEffect(() => {
    if (diffOptions.some((option) => option.mode === diffMode)) return;
    setDiffMode(defaultDiffMode(branchEntry, baseBranch));
  }, [diffOptions, diffMode, branchEntry, baseBranch]);

  const branchReview = useMemo(
    () => (branch ? { workspaceSlug, branch } : null),
    [workspaceSlug, branch],
  );

  const activeOption =
    diffOptions.find((option) => option.mode === diffMode) ?? diffOptions[0];

  const manifestQuery = useQuery({
    queryKey: ["branch-triage-diff-manifest", workspaceSlug, branch, diffMode],
    queryFn: () => fetchBranchDiffManifest(workspaceSlug, branch!, baseBranch, diffMode),
    enabled: Boolean(workspaceSlug && branch),
  });

  const manifest: DiffArtifact | null = manifestQuery.data?.diff ?? null;

  const loadFile = useCallback(
    async (filePath: string) => {
      if (!branch) return null;
      const response = await fetchBranchDiffFile(
        workspaceSlug,
        branch,
        filePath,
        baseBranch,
        diffMode,
      );
      return response.diff.sections?.[0] ?? null;
    },
    [workspaceSlug, branch, baseBranch, diffMode],
  );

  if (!branch) {
    return (
      <div className="branch-triage-empty">
        Select a branch to review its diff against {baseBranch || "main"}.
      </div>
    );
  }

  return (
    <div className="branch-triage-main branch-triage-diff-panel">
      <div className="branch-triage-summary branch-triage-diff-summary">
        <div className="branch-triage-summary-title">
          <h2>{branch}</h2>
          {branchEntry?.is_current ? <BranchTriageCurrentTag /> : null}
        </div>
        <div className="branch-triage-diff-summary-actions">
          <BranchDiffCompareMenu
            options={diffOptions}
            value={diffMode}
            disabled={manifestQuery.isLoading || manifestQuery.isFetching}
            onChange={setDiffMode}
          />
        </div>
      </div>

      {manifestQuery.isLoading || manifestQuery.isFetching ? (
        <div className="branch-triage-empty">Loading diff ({activeOption?.label ?? diffMode})…</div>
      ) : manifestQuery.error ? (
        <div className="branch-triage-empty">
          {manifestQuery.error instanceof Error ? manifestQuery.error.message : "Failed to load diff"}
        </div>
      ) : !manifest?.file_entries?.length ? (
        <div className="branch-triage-empty">
          No changes for <code>{branch}</code> ({activeOption?.label ?? diffMode}).
        </div>
      ) : branchReview ? (
        <InlineCodeDiffReview
          branchReview={branchReview}
          diff={manifest}
          lazyLoadFiles
          onLoadFile={loadFile}
          diffSummary={{
            files: manifest.files,
            range: manifest.range,
            add: manifest.add,
            del: manifest.del,
          }}
        />
      ) : null}
    </div>
  );
}
