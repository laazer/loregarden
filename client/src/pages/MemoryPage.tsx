import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { BriefingHealthPanel } from "../components/memory/BriefingHealthPanel";
import { LearningsPanel } from "../components/memory/LearningsPanel";
import { PageTopbar } from "../components/TopbarPageSlot";
import { formatLocalTimestamp } from "../lib/timestamps";
import { describeError, pushToast } from "../state/toastStore";
import "./MemoryPage.css";

/**
 * The operator surface for agent memory.
 *
 * Top: whether briefings are actually being recorded (183's telemetry), with
 * runs that recorded nothing drawn apart from the outcomes that were. Below:
 * the learnings themselves, with their observed confidence (178) and the
 * control that discredits or restores one (182's flag, which until now only
 * code could set).
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
        <LearningsPanel />
      </div>
    </div>
  );
}
