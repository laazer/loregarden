import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type { TicketDetail } from "../api/client";
import type { LedgerAttempt } from "../api/types";

import { formatApprovalResolveError } from "../utils/approvalErrors";
import { ACTIVE_LEDGER_STATUSES } from "../lib/ledgerStatus";
import { LaneLogView } from "./logs/LaneLogView";
import { LiveLogLine, LogLineRow } from "./logs/LogLineRow";
import { RunningLaneTabs } from "./logs/RunningLaneTabs";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import "./LogsPanel.css";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { useTriageSession } from "../hooks/useTriageSession";

export function LogsPanel({
  ticket,
}: {
  ticket: TicketDetail;
}) {
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const { pending } = useTriageSession(ticket.id);
  const resolveApproval = useApprovalResolution(ticket.id);

  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticket.id],
    queryFn: () => api.ticketLedger(ticket.id),
    refetchInterval: 2000,
  });

  const runningLanes = useMemo<LedgerAttempt[]>(() => {
    if (!ledger.data) return [];
    const lanes: LedgerAttempt[] = [];
    for (const visit of ledger.data.visits) {
      if (!visit.is_parallel) continue;
      for (const attempt of visit.attempts) {
        if (ACTIVE_LEDGER_STATUSES.has(attempt.status)) lanes.push(attempt);
      }
    }
    return lanes;
  }, [ledger.data]);

  // Render-time (not effect) selection adjustment: a dropped-out lane falls
  // back synchronously, in the same render, to the next remaining lane — a
  // one-frame-lagging effect would flash a blank pane.
  const laneIds = runningLanes.map((lane) => lane.run_id).join(", ");
  const laneIdsRef = useRef<string | undefined>(undefined);
  let effectiveSelectedRunId = selectedRunId;
  if (laneIdsRef.current !== laneIds) {
    laneIdsRef.current = laneIds;
    const stillPresent =
      selectedRunId !== null && runningLanes.some((lane) => lane.run_id === selectedRunId);
    if (!stillPresent) {
      effectiveSelectedRunId = runningLanes[0]?.run_id ?? null;
    }
    if (effectiveSelectedRunId !== selectedRunId) {
      setSelectedRunId(effectiveSelectedRunId);
    }
  }

  const lines = ticket.artifacts?.logs ?? [];
  const live = ticket.artifacts?.live ?? null;

  useEffect(() => {
    const node = logScrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [lines.length, live, ticket.id]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 340 }}>
      {runningLanes.length > 0 ? (
        <>
          <RunningLaneTabs
            lanes={runningLanes}
            selectedRunId={effectiveSelectedRunId}
            onSelect={setSelectedRunId}
          />
          <div role="tabpanel" style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
            {effectiveSelectedRunId && <LaneLogView key={effectiveSelectedRunId} runId={effectiveSelectedRunId} />}
          </div>
        </>
      ) : (
        <div ref={logScrollRef} style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
          <div className="log-feed">
            {lines.length === 0 && !live ? (
              <div className="log-feed-empty">
                No log lines yet. Start a stage run to stream output here.
              </div>
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
      )}

      <PendingApprovalsSection
        approvals={pending}
        ticketExternalId={ticket.external_id}
        submittingApprovalId={resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null}
        submitError={resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null}
        onApprove={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
        }
        onReject={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
        }
      />
    </div>
  );
}
