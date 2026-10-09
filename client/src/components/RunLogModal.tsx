import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { CopyValueButton } from "./CopyValueButton";
import { IconCloseButton } from "./IconCloseButton";
import { LiveLogLine, LogLineRow } from "./logs/LogLineRow";
import { RunSteerComposer } from "./RunSteerComposer";
import "./LogsPanel.css";
import { ModalShell } from "./ui/ModalShell";
import {
  LOG_FEED_EMPTY,
  LOG_FEED_ERROR,
  LOG_FEED_LOADING,
  LOG_FEED_RECONNECTING,
  detachedEmptyText,
  logFeedState,
} from "../lib/logFeedState";

const ACTIVE_STATUSES = new Set(["running", "awaiting_permission"]);

export function RunLogModal({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const isOpen = Boolean(runId);

  const log = useQuery({
    queryKey: ["run-log", runId],
    queryFn: () => api.runLog(runId!),
    enabled: isOpen,
    // A run still streaming keeps writing lines; a finished one never will.
    refetchInterval: (query) =>
      ACTIVE_STATUSES.has(query.state.data?.status?.toLowerCase() ?? "") ? 2000 : false,
  });

  if (!isOpen) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="run-log-modal-title">{null}</ModalShell>;
  }

  const data = log.data;
  const lines = data?.lines ?? [];
  const live = data?.live ?? null;
  const transport = data?.transport ?? "";
  const attachCommand = data?.attach_command ?? "";
  const isRunning = ACTIVE_STATUSES.has(data?.status?.toLowerCase() ?? "");
  // The five states are decided in one module both log panes import; fixing
  // this one alone is how the other keeps blanking on a restart.
  const feed = logFeedState({
    lines,
    live,
    isPending: log.isPending,
    isError: log.isError,
    isRunning,
    transport,
  });

  return (
    <ModalShell open onDismiss={onClose} labelledBy="run-log-modal-title" panelClassName="modal-panel-wide">
      <div className="modal-header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="state-label">Run log</div>
          <h2 id="run-log-modal-title" className="modal-title" style={{ fontFamily: "var(--mono)" }}>
            {data?.run_code ?? "—"}
          </h2>
          {data && (
            <p className="modal-subtitle">
              {data.agent_id || "—"} · {data.stage_key || "—"} · {data.status} ·{" "}
              {transport || "—"}
            </p>
          )}
        </div>
        {attachCommand ? (
          <CopyValueButton value={attachCommand} what="tmux attach command" />
        ) : null}
        <IconCloseButton onClick={onClose} />
      </div>

      <div className="modal-body" style={{ overflow: "auto", minHeight: 0 }}>
        {data?.command && (
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--txl)",
              wordBreak: "break-all",
              marginBottom: 10,
            }}
          >
            {data.command}
          </div>
        )}

        {feed === "loading" ? (
          <div className="log-feed-empty">{LOG_FEED_LOADING}</div>
        ) : feed === "error" ? (
          <div className="log-feed-empty">{LOG_FEED_ERROR}</div>
        ) : feed === "empty" ? (
          <div className="log-feed-empty">{LOG_FEED_EMPTY}</div>
        ) : feed === "empty-detached" ? (
          <div className="log-feed-empty">{detachedEmptyText(transport)}</div>
        ) : (
          <div className="log-feed">
            {feed === "reconnecting" && (
              <div className="log-feed-empty">{LOG_FEED_RECONNECTING}</div>
            )}
            {/* ux-ok: zero rows never reach this branch; logFeedState returns "empty"/"empty-detached" for them, rendered above. */}
            {lines.map((line, index) => (
              <LogLineRow key={`${line.time}-${line.tag}-${index}`} line={line} />
            ))}
            {live ? <LiveLogLine text={live} /> : null}
          </div>
        )}

        {runId && (
          <RunSteerComposer runId={runId} isActive={isRunning} />
        )}

        {data?.stderr && (
          <>
            <div className="state-label" style={{ marginTop: 14 }}>
              stderr
            </div>
            <pre
              style={{
                margin: "6px 0 0",
                fontFamily: "var(--mono)",
                fontSize: 11,
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
                color: "var(--rdl)",
              }}
            >
              {data.stderr}
            </pre>
          </>
        )}
      </div>
    </ModalShell>
  );
}
