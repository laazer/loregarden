import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "../../api/client";
import { ACTIVE_LEDGER_STATUSES } from "../../lib/ledgerStatus";
import {
  LOG_FEED_EMPTY,
  LOG_FEED_ERROR,
  LOG_FEED_LOADING,
  LOG_FEED_RECONNECTING,
  detachedEmptyText,
  logFeedState,
} from "../../lib/logFeedState";
import { useUiStore } from "../../state/uiStore";
import { Input } from "../ui/Input";
import { LiveLogLine, LogLineRow } from "./LogLineRow";

/** A single running lane's log feed — mounted only while its tab is selected. */
export function LaneLogView({ runId }: { runId: string }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const autoFollow = useUiStore((s) => s.autoFollowByRunId[runId] ?? true);
  const setAutoFollow = useUiStore((s) => s.setAutoFollow);

  const log = useQuery({
    queryKey: ["run-log", runId],
    queryFn: () => api.runLog(runId),
    refetchInterval: (query) =>
      ACTIVE_LEDGER_STATUSES.has(query.state.data?.status?.toLowerCase() ?? "") ? 2000 : false,
  });

  const lines = log.data?.lines ?? [];
  const live = log.data?.live ?? null;
  const transport = log.data?.transport ?? "";
  // The same five states the run log modal shows, from the same module: both
  // render one payload, so a divergence leaves one pane blanking on a restart.
  const feed = logFeedState({
    lines,
    live,
    isPending: log.isPending,
    isError: log.isError,
    isRunning: ACTIVE_LEDGER_STATUSES.has(log.data?.status?.toLowerCase() ?? ""),
    transport,
  });

  useEffect(() => {
    if (!autoFollow) return;
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [autoFollow, lines.length, live, runId]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div ref={scrollRef} style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <div className="log-feed">
          {feed === "loading" ? (
            <div className="log-feed-empty">{LOG_FEED_LOADING}</div>
          ) : feed === "error" ? (
            <div className="log-feed-empty">{LOG_FEED_ERROR}</div>
          ) : feed === "empty" ? (
            <div className="log-feed-empty">{LOG_FEED_EMPTY}</div>
          ) : feed === "empty-detached" ? (
            <div className="log-feed-empty">{detachedEmptyText(transport)}</div>
          ) : (
            <>
              {feed === "reconnecting" && (
                <div className="log-feed-empty">{LOG_FEED_RECONNECTING}</div>
              )}
              {lines.map((line, index) => (
                <LogLineRow key={`${line.time}-${line.tag}-${index}`} line={line} />
              ))}
              {live ? <LiveLogLine text={live} /> : null}
            </>
          )}
        </div>
      </div>

      <label className="chat-composer-option" style={{ padding: "6px 16px" }}>
        <Input
          type="checkbox"
          checked={autoFollow}
          onChange={(e) => setAutoFollow(runId, e.target.checked)}
        />
        Auto-follow
      </label>
    </div>
  );
}
