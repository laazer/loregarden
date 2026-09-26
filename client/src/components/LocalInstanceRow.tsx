import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { localInstancesApi } from "../api/localInstancesApi";
import type { LocalInstance, LocalInstanceState } from "../api/localInstancesTypes";
import { describeError } from "../state/toastStore";

const STATE_LABEL: Record<LocalInstanceState, string> = {
  starting: "Starting",
  ready: "Ready",
  stalled: "Not ready",
  exited: "Exited",
};

interface LocalInstanceRowProps {
  instance: LocalInstance;
  /** The server a client proxies to, by name. */
  targetName?: string;
  stopping: boolean;
  onStop: (instance: LocalInstance) => void;
}

function LogTail({ id }: { id: string }) {
  const logs = useQuery({ queryKey: ["local-instances", "logs", id], queryFn: () => localInstancesApi.logs(id) });
  if (logs.error) {
    return (
      <p className="local-instances-error" role="alert">
        {describeError(logs.error, "Could not load the log")}
      </p>
    );
  }
  if (!logs.data) return <p className="modal-hint">Loading log…</p>;
  if (logs.data.lines.length === 0) {
    return <p className="modal-hint">The log is empty — the process has written nothing yet.</p>;
  }
  return (
    <pre className="local-instances-log" tabIndex={0} aria-label={`Log for ${id}`}>
      {logs.data.truncated ? "…\n" : ""}
      {logs.data.lines.join("\n")}
    </pre>
  );
}

export function LocalInstanceRow({ instance, targetName, stopping, onStop }: LocalInstanceRowProps) {
  const [showLog, setShowLog] = useState(false);
  const exited = instance.state === "exited";
  const logId = `local-instance-log-${instance.id}`;

  return (
    <li className="local-instances-row">
      <div className="local-instances-row-head">
        <span className={`local-instances-state local-instances-state--${instance.state}`}>
          {STATE_LABEL[instance.state]}
        </span>
        <strong>{instance.name}</strong>
        <span className="local-instances-meta">
          {instance.role} {instance.kind}
          {targetName !== undefined ? ` → ${targetName}` : ""}
        </span>
      </div>
      <div className="local-instances-row-detail">
        {exited ? (
          <span className="local-instances-meta">{instance.url}</span>
        ) : (
          <a href={instance.url} target="_blank" rel="noreferrer">
            {instance.url}
          </a>
        )}
        {Object.entries(instance.labels)
          .filter(([, value]) => value)
          .map(([key, value]) => (
            <span key={key} className="local-instances-meta">
              {key}: {value}
            </span>
          ))}
      </div>
      {instance.last_error && (
        <p className="local-instances-error" role={exited ? "alert" : undefined}>
          {instance.last_error}
        </p>
      )}
      {instance.state === "stalled" && (
        <p className="modal-hint">
          Running, but not answering {instance.health_path ?? "on its port"} after {instance.ready_timeout_seconds}s.
          Its log usually says why.
        </p>
      )}
      <div className="local-instances-row-actions">
        {instance.log_path !== null && (
          <button
            type="button"
            className="btn-secondary"
            aria-expanded={showLog}
            aria-controls={logId}
            onClick={() => setShowLog((open) => !open)}
          >
            {showLog ? "Hide log" : "Log"}
          </button>
        )}
        {instance.managed ? (
          <button
            type="button"
            className="btn-secondary"
            disabled={stopping}
            aria-busy={stopping}
            onClick={() => onStop(instance)}
          >
            {stopping ? "Stopping…" : exited ? "Dismiss" : "Stop"}
          </button>
        ) : (
          <span className="local-instances-meta" title="It registered itself; stop it where it was started.">
            Not managed here
          </span>
        )}
      </div>
      {showLog && (
        <div id={logId}>
          <LogTail id={instance.id} />
        </div>
      )}
    </li>
  );
}
