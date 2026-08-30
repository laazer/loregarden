import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { BranchTriageDiffPanel } from "../components/BranchTriageDiffPanel";
import { BranchTriageOverviewPanel } from "../components/BranchTriageOverviewPanel";
import { BranchTriageList } from "../components/BranchTriageList";
import { PageTopbar } from "../components/TopbarPageSlot";
import { fetchBranchTriage } from "../lib/branchTriageApi";
import { useUiStore } from "../state/uiStore";
import "../components/BranchTriagePanel.css";
import { AddToTabMenu } from "../components/AddToTabMenu";

type BranchTriageTab = "triage" | "diff";

const BRANCH_TRIAGE_STALE_MS = 60_000;

export function BranchTriagePage() {
  const workspaceSlug = useUiStore((s) => s.workspace);
  const branchTriageWorkspaceSlug = useUiStore((s) => s.branchTriageWorkspaceSlug);
  const setBranchTriageWorkspaceSlug = useUiStore((s) => s.setBranchTriageWorkspaceSlug);

  const [activeTab, setActiveTab] = useState<BranchTriageTab>("triage");
  // Held in the store rather than locally so the copilot dock — which mounts
  // above the routes — can bind to this branch's conversation.
  const selectedBranch = useUiStore((s) => s.branchTriageBranch) || null;
  const setSelectedBranch = useUiStore((s) => s.setBranchTriageBranch);

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });

  const activeSlug = useMemo(() => {
    if (branchTriageWorkspaceSlug) return branchTriageWorkspaceSlug;
    if (workspaceSlug && workspaceSlug !== "all") return workspaceSlug;
    return workspaces.data?.[0]?.slug ?? "";
  }, [branchTriageWorkspaceSlug, workspaceSlug, workspaces.data]);

  const activeWorkspace = useMemo(
    () => workspaces.data?.find((ws) => ws.slug === activeSlug) ?? null,
    [workspaces.data, activeSlug],
  );

  useEffect(() => {
    if (!branchTriageWorkspaceSlug && activeSlug) {
      setBranchTriageWorkspaceSlug(activeSlug);
    }
  }, [activeSlug, branchTriageWorkspaceSlug, setBranchTriageWorkspaceSlug]);

  const triage = useQuery({
    queryKey: ["branch-triage", activeSlug],
    queryFn: () => fetchBranchTriage(activeSlug),
    enabled: Boolean(activeSlug),
    // A snapshot walks every branch in the repo, so leaving it fresh for a
    // while keeps navigating back to this page — or refocusing the window —
    // from re-scanning and blanking the list behind "Scanning branches…".
    staleTime: BRANCH_TRIAGE_STALE_MS,
    refetchOnWindowFocus: false,
  });

  const selectedBranchEntry = useMemo(
    () => triage.data?.branches.find((item) => item.name === selectedBranch) ?? null,
    [triage.data?.branches, selectedBranch],
  );

  useEffect(() => {
    setSelectedBranch("");
    setActiveTab("triage");
  }, [activeSlug, setSelectedBranch]);

  useEffect(() => {
    if (!triage.data?.current_branch || selectedBranch) return;
    if (triage.data.workspace_slug !== activeSlug) return;
    setSelectedBranch(triage.data.current_branch);
  }, [
    triage.data?.current_branch,
    triage.data?.workspace_slug,
    activeSlug,
    selectedBranch,
    setSelectedBranch,
  ]);

  useEffect(() => {
    if (!selectedBranch || !triage.data?.branches) return;
    if (!triage.data.branches.some((item) => item.name === selectedBranch)) {
      setSelectedBranch("");
    }
  }, [selectedBranch, triage.data?.branches, setSelectedBranch]);

  const handleReviewBranch = (branch: string) => {
    setSelectedBranch(branch);
    setActiveTab("diff");
  };

  return (
    <div className="screen-view">
      <PageTopbar title="Branch cleanup">
        {triage.data ? (
          <span className="topbar-page-note">
            {triage.data.issue_count} branch(es) need attention
          </span>
        ) : null}
        <button
          type="button"
          className="btn-secondary"
          onClick={() => triage.refetch()}
          disabled={!activeSlug || triage.isFetching}
        >
          {triage.isFetching ? "Scanning…" : "Rescan"}
        </button>
        <label className="topbar-workspace-picker">
          <span className="topbar-workspace-picker-label">Workspace</span>
          <select
            className="btn-secondary topbar-workspace-picker-select"
            value={activeSlug}
            disabled={!workspaces.data?.length}
            aria-label="Branch triage workspace"
            onChange={(event) => setBranchTriageWorkspaceSlug(event.target.value)}
          >
            {(workspaces.data ?? []).map((ws) => (
              <option key={ws.slug} value={ws.slug}>
                {ws.name}
              </option>
            ))}
          </select>
        </label>
      </PageTopbar>

      <div className="branch-triage-page-body">
        {!activeWorkspace ? (
          <div className="branch-triage-empty">
            {workspaces.isLoading
              ? "Loading workspaces…"
              : "Add a workspace in the IDE before using Branch Triage."}
          </div>
        ) : triage.isLoading ? (
          <div className="branch-triage-empty">Scanning branches…</div>
        ) : triage.error ? (
          <div className="branch-triage-empty">
            {triage.error instanceof Error ? triage.error.message : "Failed to load branch triage"}
          </div>
        ) : (
          <>
            <div className="branch-triage-tabs">
              <button
                type="button"
                className={`branch-triage-tab ${activeTab === "triage" ? "active" : ""}`}
                onClick={() => setActiveTab("triage")}
              >
                Triage
              </button>
              <button
                type="button"
                className={`branch-triage-tab ${activeTab === "diff" ? "active" : ""}`}
                onClick={() => setActiveTab("diff")}
              >
                Diff with reviews
              </button>
              {selectedBranch === null || activeSlug === null ? null : (
                /* Both repository primitives take a workspace *and* a branch,
                   and this is the one page that has both selected at once —
                   which is exactly the pair the branch history pane refuses to
                   render without. */
                <AddToTabMenu
                  primitiveId="chat_branch_history"
                  values={
                    new Map([
                      ["workspace_slug", activeSlug],
                      ["branch", selectedBranch],
                    ])
                  }
                  title={selectedBranch}
                  label={`Add ${selectedBranch} history to a tab`}
                />
              )}
            </div>

            <div className="branch-triage-layout">
              <BranchTriageList
                workspaceSlug={activeSlug}
                branches={triage.data?.branches ?? []}
                selectedBranch={selectedBranch}
                onSelectBranch={setSelectedBranch}
                onReviewBranch={handleReviewBranch}
                onBranchDeleted={(branch) => {
                  if (selectedBranch === branch) setSelectedBranch("");
                }}
              />

              {activeTab === "diff" ? (
                <BranchTriageDiffPanel
                  workspaceSlug={activeSlug}
                  branch={selectedBranch}
                  baseBranch={triage.data?.base_branch ?? "main"}
                  branchEntry={selectedBranchEntry ?? undefined}
                />
              ) : selectedBranch ? (
                <BranchTriageOverviewPanel
                  workspaceSlug={activeSlug}
                  branch={selectedBranch}
                  baseBranch={triage.data?.base_branch ?? "main"}
                  branchEntry={selectedBranchEntry ?? undefined}
                  onReviewDiff={() => setActiveTab("diff")}
                />
              ) : (
                <div className="branch-triage-main branch-triage-empty">
                  Pick a branch to inspect its state, then chat about it in the bar below.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
