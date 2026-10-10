import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import { NotThisTarget } from "../lib/agentActions/registry";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { useAgentAction } from "../lib/agentActions/useAgentAction";

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
  const [confirming, setConfirming] = useState(false);
  // Set in the click handler itself, before React has re-rendered anything: a
  // `disabled` driven by mutation state is still false for every press inside
  // one tick, and three presses were three POSTs. After this ticket each one
  // signals a real process group the server no longer owns.
  const stopSignalled = useRef(false);
  const [stopPressed, setStopPressed] = useState(false);

  const state = useQuery({
    queryKey: ["run-messages", runId],
    queryFn: () => api.runMessages(runId),
    // Poll only while the run could still pick a message up — this is how
    // "queued" becomes "delivered" in the UI.
    refetchInterval: isActive ? 2000 : false,
  });

  const send = useMutation({
    meta: { errorTitle: "Send steer message" },
    mutationFn: (content: string) => api.sendRunMessage(runId, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-messages", runId] });
    },
  });

  // The draft clears only when the operator's own Send went out — an agent's
  // steer must not wipe what the operator is typing.
  const sendDraft = (content: string) => send.mutate(content, { onSuccess: () => setDraft("") });

  // One composer per run on screen; each answers only for its own run.
  const ownRun = (requested: string) => {
    if (requested !== runId) throw new NotThisTarget(`no steering composer for run ${requested} is on screen`);
    if (!isActive) throw new Error(`run ${runId} is not running`);
  };
  useAgentAction("run.send_message", async ({ run_id, message }) => {
    ownRun(run_id);
    // RunMessage has no author: the marker is how the run, and the operator
    // reading it, know this steer did not come from a person.
    return send.mutateAsync(`[from an agent] ${message}`);
  });
  useAgentAction("run.cancel", async ({ run_id }) => {
    ownRun(run_id);
    await requestStop();
    return { run_id, cancelled: true };
  });

  const stop = useMutation({
    meta: { errorTitle: "Stop this run" },
    mutationFn: () => api.cancelRun(runId),
    onSuccess: () => {
      setConfirming(false);
      // The refusal this run reports changes the moment the stop lands, so the
      // composer re-reads rather than leaving an input the server will refuse.
      qc.invalidateQueries({ queryKey: ["run-messages", runId] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
    onError: () => {
      // The signal never left, so the control must be pressable again — the
      // error is shown beside it either way.
      stopSignalled.current = false;
      setStopPressed(false);
    },
  });

  const requestStop = () => {
    if (stopSignalled.current) return Promise.resolve();
    stopSignalled.current = true;
    setStopPressed(true);
    return stop.mutateAsync();
  };

  // Latched off the run's own record, not the mutation: `isPending` is false
  // again the instant the POST resolves, and `cancel_requested_at` only
  // appears on the next 2s poll. A control latched off the mutation alone is
  // clickable in between, and a detached agent takes longer to die than that.
  const stopRequested = stopPressed || Boolean(state.data?.cancel_requested_at);

  // Ending a turn is not undoable and the button sits beside a text input, so
  // it asks once. A run minutes deep should not be lost to a misclick meant
  // for the message box.
  const stopControl = isActive ? (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
      {stopRequested ? (
        <Button variant="secondary" disabled>
          Stopping…
        </Button>
      ) : confirming ? (
        <>
          <Button
            variant="primary"
            style={{ background: "var(--rd)" }}
            onClick={() => void requestStop()}
          >
            Confirm stop
          </Button>
          <Button variant="secondary" onClick={() => setConfirming(false)}>
            Keep going
          </Button>
        </>
      ) : (
        <Button variant="secondary" onClick={() => setConfirming(true)}>
          Stop this run
        </Button>
      )}
      {stop.isError && (
        <span className="modal-subtitle" style={{ color: "var(--rdl)" }}>
          {(stop.error as Error).message}
        </span>
      )}
    </div>
  ) : null;

  // Render nothing until the server has said whether this run can be steered.
  // Defaulting to "yes" while loading flashed an input onto runs that cannot
  // take one, inviting a message that would have been refused.
  if (!state.data) return null;

  const { refusal, messages } = state.data;
  // A message queued behind a stop is either dropped unread or is the last
  // thing an agent is told before being killed. Neither was asked for.
  const canSend = !refusal && !stopRequested && draft.trim().length > 0 && !send.isPending;

  // Nothing to say and nothing sendable. A live run is still stoppable though:
  // a cursor-adapter run cannot take a message and must not therefore be
  // unstoppable, which is why the stop is not nested inside the steer UI.
  if (refusal && messages.length === 0) {
    if (!isActive) return null;
    return (
      <div style={{ marginTop: 12 }}>
        <p className="modal-subtitle">{refusal}</p>
        {stopControl}
      </div>
    );
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
            if (canSend) sendDraft(draft.trim());
          }}
        >
          <Input
            aria-label="Message to this run"
            className="btn-secondary filter-select"
            style={{ flex: 1, fontSize: 12.5 }}
            placeholder="e.g. use the existing helper in services/"
            value={draft}
            disabled={send.isPending || stopRequested}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button type="submit" variant="primary" disabled={!canSend}>
            {send.isPending ? "Sending…" : "Send"}
          </Button>
        </form>
      )}

      {send.isError && (
        <p className="modal-subtitle" style={{ marginTop: 6, color: "var(--rdl)" }}>
          {(send.error as Error).message}
        </p>
      )}

      {stopControl}
    </div>
  );
}
