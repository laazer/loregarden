import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { api } from "../api/client";
import { MemoryMapTab } from "../components/knowledge/MemoryMapTab";
import { LearningsPanel } from "../components/memory/LearningsPanel";
import { MemoryHealthTab } from "../components/memory/MemoryHealthTab";
import { PageTopbar } from "../components/TopbarPageSlot";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import {
  MEMORY_TABS,
  memoryPath,
  memoryPathForLegacyKnowledge,
  memoryTabFromPath,
  type MemoryTab,
} from "../lib/appNavigation";
import { describeError } from "../state/toastStore";
import "./MemoryPage.css";

const TAB_LABELS: Record<MemoryTab, string> = {
  map: "Map",
  records: "Records",
  health: "Health",
};

function RecordsTab({ slug }: { slug: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return (
    <>
      <PageTopbar title="Memory" />
      <LearningsPanel workspaceSlug={slug} selectedId={selectedId} onSelect={setSelectedId} />
    </>
  );
}

function ActiveTab({ tab, slug }: { tab: MemoryTab; slug: string }) {
  if (tab === "records") return <RecordsTab key={slug} slug={slug} />;
  if (tab === "health") return <MemoryHealthTab key={slug} slug={slug} />;
  return <MemoryMapTab key={slug} slug={slug} />;
}

/**
 * Everything about agent memory, in one place: the map of what is recorded
 * and how it links, the records themselves with their confidence and
 * discredit/restore control, and whether memory is being recorded and used at
 * all. One workspace picker scopes all three; the tab is in the URL.
 */
export function MemoryPage() {
  const location = useLocation();
  const tab = memoryTabFromPath(location.pathname);
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    meta: { errorTitle: "Load workspaces" },
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const slug = chosen ?? workspaces.data?.[0]?.slug ?? null;

  let body;
  if (workspaces.isLoading) {
    body = <PaneSkeleton variant="list" rows={6} label="Loading workspaces…" />;
  } else if (!workspaces.data) {
    body = (
      <div className="kb-state" role="alert">
        <p>Could not load workspaces: {describeError(workspaces.error, "the request failed")}.</p>
        <button type="button" className="btn-secondary" onClick={() => void workspaces.refetch()}>
          Try again
        </button>
      </div>
    );
  } else if (!slug) {
    body = <p className="kb-state">No workspaces yet. Add one to start recording memory.</p>;
  } else {
    body = <ActiveTab tab={tab} slug={slug} />;
  }

  return (
    <div className={`screen-view screen-view--memory screen-view--memory-${tab}`}>
      <div className="memory-header">
        <nav className="memory-tabs" aria-label="Memory views">
          {MEMORY_TABS.map((option) => (
            <Link
              key={option}
              to={memoryPath(option)}
              className={`memory-tab${option === tab ? " active" : ""}`}
              aria-current={option === tab ? "page" : undefined}
            >
              {TAB_LABELS[option]}
            </Link>
          ))}
        </nav>
        {workspaces.data && slug && workspaces.data.length > 1 && (
          <label className="kb-workspace">
            <span>Workspace</span>
            <select value={slug} onChange={(event) => setChosen(event.target.value)}>
              {workspaces.data.map((ws) => (
                <option key={ws.id} value={ws.slug}>
                  {ws.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {workspaces.data && slug && workspaces.data.length === 1 && (
          <span className="memory-workspace-name">{workspaces.data[0].name}</span>
        )}
      </div>
      <div className="memory-page-body">{body}</div>
    </div>
  );
}

/** `/knowledge[/:nodeId]` was the map's first home; send it to the map. */
export function LegacyKnowledgeRedirect() {
  const location = useLocation();
  return <Navigate replace to={memoryPathForLegacyKnowledge(location.pathname)} />;
}
