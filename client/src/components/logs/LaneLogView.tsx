import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "../../api/client";
import { ACTIVE_LEDGER_STATUSES } from "../../lib/ledgerStatus";
import { useUiStore } from "../../state/uiStore";
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
          {log.isPending ? (
            <div className="log-feed-empty">Loading log…</div>
          ) : log.isError ? (
            <div className="log-feed-empty">Could not load this run&rsquo;s log.</div>
          ) : lines.length === 0 && !live ? (
            <div className="log-feed-empty">No log recorded for this run.</div>
          ) : (
            <>
              {lines.map((line, index) => (
                <LogLineRow key={`${line.time}-${line.tag}-${index}`} line={line} />
              ))}
              {live ? <LiveLogLine text={live} /> : null}
            </>
          )}
        </div>
      </div>

      <label className="chat-composer-option" style={{ padding: "6px 16px" }}>
        <input
          type="checkbox"
          checked={autoFollow}
          onChange={(e) => setAutoFollow(runId, e.target.checked)}
        />
        Auto-follow
      </label>
    </div>
  );
}
