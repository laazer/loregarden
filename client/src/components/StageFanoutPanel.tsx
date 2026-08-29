/**
 * Where a fan-out is started and where its verdict is given.
 *
 * Deliberately understated: N attempts cost N times the tokens, so this offers
 * one stage at a time and says what it will spend rather than presenting
 * itself as a normal way to run a stage. The narrow case it exists for — a
 * hard implement stage, or one that has already burned a rework cycle — is
 * stated in the panel itself so nobody has to go and read the ticket.
 *
 * While a fan-out is unsettled the launcher is gone: the outstanding decision
 * is the only thing to do next.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { StageFanoutReview } from "./StageFanoutReview";
import { describeError } from "../state/toastStore";
import { stageFanoutApi, type FanoutGroup } from "../lib/stageFanoutApi";
import "./StageFanoutPanel.css";

interface StageOption {
  key: string;
  name: string;
}

interface Props {
  ticketId: string;
  stages: StageOption[];
}

interface FanoutList {
  groups: FanoutGroup[];
  open_group_id: string | null;
}

const ATTEMPT_CHOICES = [2, 3, 4, 5];

export function StageFanoutPanel({ ticketId, stages }: Props) {
  const qc = useQueryClient();
  const [stageKey, setStageKey] = useState(stages[0]?.key ?? "");
  const [attempts, setAttempts] = useState(2);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState("");

  const list = useQuery<FanoutList>({
    queryKey: ["stage-fanout", ticketId],
    queryFn: () => stageFanoutApi.list(ticketId),
    enabled: !!ticketId,
  });

  const open = list.data?.groups.find((group) => group.id === list.data?.open_group_id);

  async function launch() {
    if (!stageKey) return;
    setLaunching(true);
    setError("");
    try {
      await stageFanoutApi.launch(ticketId, { stage_key: stageKey, attempt_count: attempts });
      await qc.invalidateQueries({ queryKey: ["stage-fanout", ticketId] });
    } catch (err) {
      setError(describeError(err, "Could not start the fan-out"));
    } finally {
      setLaunching(false);
    }
  }

  return (
    <section className="fanout-panel" aria-label="Competing attempts">
      <header >
        <span className="fanout-panel__title">Competing attempts</span>
        <span className="fanout-panel__hint">
          Runs one stage several times in separate worktrees and keeps one. Worth the N× tokens on a
          hard stage, or one that has already been reworked — not as a default.
        </span>
      </header>

      {error && (
        <p className="fanout-panel__error" role="alert">
          {error}
        </p>
      )}

      {open ? (
        <StageFanoutReview
          group={open}
          onSettled={() => qc.invalidateQueries({ queryKey: ["stage-fanout", ticketId] })}
        />
      ) : (
        <div className="fanout-panel__launcher">
          <label>
            Stage
            <select value={stageKey} onChange={(event) => setStageKey(event.target.value)}>
              {stages.map((stage) => (
                <option key={stage.key} value={stage.key}>
                  {stage.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Attempts
            <select
              value={attempts}
              onChange={(event) => setAttempts(Number(event.target.value))}
              aria-label="Attempts"
            >
              {ATTEMPT_CHOICES.map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={launching || !stageKey}
            onClick={launch}
          >
            {launching ? `Running ${attempts} attempts…` : `Run ${attempts} attempts`}
          </button>
        </div>
      )}

      {list.data && list.data.groups.length > 0 && !open ? (
        <p className="fanout-panel__history">
          {list.data.groups.length} settled fan-out(s) on this ticket — last one{" "}
          {list.data.groups[0].outcome} on <code>{list.data.groups[0].stage_key}</code>.
        </p>
      ) : null}
    </section>
  );
}
