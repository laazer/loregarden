/**
 * The Memory page's health tab: whether briefings are being recorded at all
 * (183's telemetry, across every workspace), then the chosen workspace's graph
 * shape against its last snapshot and the maintenance worth a decision.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import { memoryPath } from "../../lib/appNavigation";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError, pushToast } from "../../state/toastStore";
import { PageTopbar } from "../TopbarPageSlot";
import { BriefingHealthPanel } from "./BriefingHealthPanel";
import { GraphHealthPanel } from "./GraphHealthPanel";
import { ProposalsPanel } from "./ProposalsPanel";

export function MemoryHealthTab({ slug }: { slug: string }) {
  const navigate = useNavigate();
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
    <>
      <PageTopbar title="Memory">
        <span className="memory-checked" aria-live="polite">
          Last checked {checkedAt ? formatLocalTimestamp(checkedAt) : "—"}
        </span>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={stats.isFetching}
          aria-busy={stats.isFetching}
          onClick={() => void refresh()}
        >
          {stats.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </PageTopbar>
      <BriefingHealthPanel
        stats={stats.data}
        isLoading={stats.isLoading}
        error={stats.error}
        windowDays={windowDays}
        onWindowChange={setWindowDays}
        onRetry={() => void refresh()}
      />
      <GraphHealthPanel workspaceSlug={slug} />
      <ProposalsPanel
        workspaceSlug={slug}
        onOpen={(nodeId) => navigate(memoryPath("map", nodeId))}
      />
    </>
  );
}
