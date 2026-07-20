import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";

/**
 * Sends a correction to a run that is already going.
 *
 * A stage used to be a closed box: once started, the only options were to wait
 * or to kill it. When the log shows an agent has misread the task, a sentence
 * is a cheaper fix than a rerun.
 *
 * Only claude-adapter runs can receive input — cursor-agent has no
 * `--input-format`, so there is no channel to write into a run it is executing.
 * The server returns that reason, and it is shown here rather than accepting a
 * message that would silently go nowhere.
 */
export function RunSteerComposer({ runId, isActive }: { runId: string; isActive: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const state = useQuery({
    queryKey: ["run-messages", runId],
    queryFn: () => api.runMessages(runId),
    // Poll only while the run could still pick a message up — this is how
    // "queued" becomes "delivered" in the UI.
    refetchInterval: isActive ? 2000 : false,
  });

  const send = useMutation({
    mutationFn: (content: string) => api.sendRunMessage(runId, content),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["run-messages", runId] });
    },
  });

  // Render nothing until the server has said whether this run can be steered.
  // Defaulting to "yes" while loading flashed an input onto runs that cannot
  // take one, inviting a message that would have been refused.
  if (!state.data) return null;

  const { refusal, messages } = state.data;
  const canSend = !refusal && draft.trim().length > 0 && !send.isPending;

  // Nothing to say and nothing sendable: stay out of the way entirely.
  if (refusal && messages.length === 0) {
    return isActive ? (
      <p className="modal-subtitle" style={{ marginTop: 12 }}>
        {refusal}
      </p>
    ) : null;
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div className="state-label">Steer this run</div>

      {messages.length > 0 && (
        <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none" }}>
          {messages.map((message) => (
            <li
              key={message.id}
              style={{ fontSize: 12, color: "var(--txl)", padding: "3px 0", lineHeight: 1.5 }}
            >
              <span style={{ color: "var(--tx)" }}>{message.content}</span>{" "}
              <span style={{ fontFamily: "var(--mono)", fontSize: 10 }}>
                {message.delivered_at ? "· delivered" : "· queued"}
              </span>
            </li>
          ))}
        </ul>
      )}

      {refusal ? (
        <p className="modal-subtitle" style={{ marginTop: 8 }}>
          {refusal}
        </p>
      ) : (
        <form
          style={{ display: "flex", gap: 8, marginTop: 8 }}
          onSubmit={(event) => {
            event.preventDefault();
            if (canSend) send.mutate(draft.trim());
          }}
        >
          <input
            aria-label="Message to this run"
            className="btn-secondary filter-select"
            style={{ flex: 1, fontSize: 12.5 }}
            placeholder="e.g. use the existing helper in services/"
            value={draft}
            disabled={send.isPending}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" className="btn-primary" disabled={!canSend}>
            {send.isPending ? "Sending…" : "Send"}
          </button>
        </form>
      )}

      {send.isError && (
        <p className="modal-subtitle" style={{ marginTop: 6, color: "var(--rdl)" }}>
          {(send.error as Error).message}
        </p>
      )}
    </div>
  );
}
