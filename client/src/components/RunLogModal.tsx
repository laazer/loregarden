import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "../api/client";
import { IconCloseButton } from "./IconCloseButton";
import { LiveLogLine, LogLineRow } from "./logs/LogLineRow";
import "./LogsPanel.css";

const ACTIVE_STATUSES = new Set(["running", "awaiting_permission"]);

export function RunLogModal({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const isOpen = Boolean(runId);

  const log = useQuery({
    queryKey: ["run-log", runId],
    queryFn: () => api.runLog(runId!),
    enabled: isOpen,
    // A run still streaming keeps writing lines; a finished one never will.
    refetchInterval: (query) =>
      ACTIVE_STATUSES.has(query.state.data?.status?.toLowerCase() ?? "") ? 2000 : false,
  });

  useEffect(() => {
    if (!isOpen) return;
    panelRef.current?.focus();
  }, [isOpen, runId]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const data = log.data;
  const lines = data?.lines ?? [];
  const live = data?.live ?? null;

  return (
    <>
      <div className="modal-overlay" data-testid="modal-backdrop" onClick={onClose} role="presentation" />
      <div
        ref={panelRef}
        className="modal-panel modal-panel-wide"
        role="dialog"
        aria-labelledby="run-log-modal-title"
        aria-modal="true"
        tabIndex={-1}
        data-testid="modal-content"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="state-label">Run log</div>
            <h2 id="run-log-modal-title" className="modal-title" style={{ fontFamily: "var(--mono)" }}>
              {data?.run_code ?? "—"}
            </h2>
            {data && (
              <p className="modal-subtitle">
                {data.agent_id || "—"} · {data.stage_key || "—"} · {data.status}
              </p>
            )}
          </div>
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

          {log.isPending ? (
            <div className="log-feed-empty">Loading log…</div>
          ) : log.isError ? (
            <div className="log-feed-empty">Could not load this run&rsquo;s log.</div>
          ) : lines.length === 0 && !live ? (
            <div className="log-feed-empty">No log recorded for this run.</div>
          ) : (
            <div className="log-feed">
              {lines.map((line, index) => (
                <LogLineRow key={`${line.time}-${line.tag}-${index}`} line={line} />
              ))}
              {live ? <LiveLogLine text={live} /> : null}
            </div>
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
      </div>
    </>
  );
}
