import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { BriefingHealthPanel } from "../components/memory/BriefingHealthPanel";
import { GraphHealthPanel } from "../components/memory/GraphHealthPanel";
import { LearningsPanel } from "../components/memory/LearningsPanel";
import { ProposalsPanel } from "../components/memory/ProposalsPanel";
import { PageTopbar } from "../components/TopbarPageSlot";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { formatLocalTimestamp } from "../lib/timestamps";
import { describeError, pushToast } from "../state/toastStore";
import "./MemoryPage.css";

/** Everything scoped to one workspace's graph, under one picker. */
function WorkspaceMemory() {
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    meta: { errorTitle: "Load workspaces" },
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const slug = chosen ?? workspaces.data?.[0]?.slug ?? null;

  if (workspaces.isLoading) {
    return <PaneSkeleton variant="list" rows={4} label="Loading workspaces…" />;
  }
  if (!workspaces.data) {
    return (
      <p className="memory-error" role="alert">
        Could not load workspaces: {describeError(workspaces.error, "the request failed")}.
      </p>
    );
  }
  if (!slug) {
    return <p className="memory-empty">No workspaces yet. Add one to start recording memory.</p>;
  }
  return (
    <>
      <label className="memory-workspace">
        <span>Workspace</span>
        <select
          value={slug}
          onChange={(event) => {
            setChosen(event.target.value);
            setSelectedId(null);
          }}
        >
          {workspaces.data.map((ws) => (
            <option key={ws.id} value={ws.slug}>
              {ws.name}
            </option>
          ))}
        </select>
      </label>
      <GraphHealthPanel key={`health:${slug}`} workspaceSlug={slug} />
      <ProposalsPanel key={`proposals:${slug}`} workspaceSlug={slug} onOpen={setSelectedId} />
      <LearningsPanel
        key={`learnings:${slug}`}
        workspaceSlug={slug}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
    </>
  );
}

/**
 * The operator surface for agent memory.
 *
 * Top: whether briefings are actually being recorded (183's telemetry), with
 * runs that recorded nothing drawn apart from the outcomes that were. Then, for
 * one workspace: the graph's shape against its last snapshot, the maintenance
 * worth a person's decision, and the learnings themselves — with observed
 * confidence (178), relations, lineage, and discredit/restore.
 */
export function MemoryPage() {
  const [windowDays, setWindowDays] = useState(7);
  const stats = useQuery({
    queryKey: ["memory-briefings", windowDays],
    queryFn: () => api.memoryBriefings(windowDays),
    meta: { errorTitle: "Load memory health" },
  });

  // The global query toast stays quiet when stale data is on screen, so an
  // explicit refresh reports its own failure — otherwise a click that failed
  // would look exactly like one that found nothing new.
  const refresh = async () => {
    const result = await stats.refetch();
    if (result.error && result.data !== undefined) {
      pushToast({
        tone: "error",
        title: "Refresh memory health failed",
        message: describeError(result.error, "The request failed"),
      });
    }
  };

  const checkedAt = stats.dataUpdatedAt ? new Date(stats.dataUpdatedAt).toISOString() : null;

  return (
    <div className="screen-view screen-view--memory">
      <PageTopbar title="Memory">
        <span className="memory-checked" aria-live="polite">
          Last checked {checkedAt ? formatLocalTimestamp(checkedAt) : "—"}
        </span>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={stats.isFetching}
          onClick={() => void refresh()}
        >
          {stats.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </PageTopbar>
      <div className="memory-page-body">
        <BriefingHealthPanel
          stats={stats.data}
          isLoading={stats.isLoading}
          error={stats.error}
          windowDays={windowDays}
          onWindowChange={setWindowDays}
          onRetry={() => void refresh()}
        />
        <WorkspaceMemory />
      </div>
    </div>
  );
}
