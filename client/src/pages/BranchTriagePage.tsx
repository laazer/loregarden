import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { PageHeroAppToolbar } from "../components/PageHeroAppToolbar";
import { BranchTriageChatPanel } from "../components/BranchTriageChatPanel";
import { BranchTriageDiffPanel } from "../components/BranchTriageDiffPanel";
import { BranchTriageList } from "../components/BranchTriageList";
import { fetchBranchTriage } from "../lib/branchTriageApi";
import { useUiStore } from "../state/uiStore";
import "../components/BranchTriagePanel.css";

type BranchTriageTab = "triage" | "diff";

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
    <div className="screen-view screen-view--branch-triage">
      <header className="page-hero-header">
        <div className="page-hero-copy">
          <div className="page-hero-eyebrow">
            <span>Branch Triage</span>
            <span className="page-hero-eyebrow-dot" aria-hidden />
            <span className="page-hero-eyebrow-muted">Triage · Diff review</span>
          </div>
          <h1 className="page-hero-title">Branch cleanup</h1>
          <p className="page-hero-sub">
            Workspace: <span style={{ color: "var(--tx)" }}>{activeWorkspace?.name ?? "—"}</span>
            {triage.data ? (
              <>
                {" "}
                · <span style={{ color: "var(--txm)" }}>{triage.data.issue_count} branch(es) need attention</span>
              </>
            ) : null}
          </p>
        </div>
        <div className="page-hero-actions">
          <label className="editor-workspace-picker">
            <span className="page-hero-field-label">Workspace</span>
            <select
              className="btn-secondary page-hero-field-select"
              value={activeSlug}
              disabled={!workspaces.data?.length}
              onChange={(event) => setBranchTriageWorkspaceSlug(event.target.value)}
            >
              {(workspaces.data ?? []).map((ws) => (
                <option key={ws.slug} value={ws.slug}>
                  {ws.name}
                </option>
              ))}
            </select>
          </label>
          <PageHeroAppToolbar />
        </div>
      </header>

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
                <BranchTriageChatPanel
                  workspaceSlug={activeSlug}
                  branch={selectedBranch}
                  branchEntry={selectedBranchEntry ?? undefined}
                />
              ) : (
                <div className="branch-triage-main branch-triage-empty">
                  Pick a branch to inspect issues and chat with triage.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
