/**
 * How many lanes run at once.
 *
 * Answers "how much can the machine run in parallel, and can I change it?".
 * Lowering the count never stops work: a busy lane past the new count finishes
 * its ticket, then retires, and anything waiting in it moves to a lane that
 * stays — so the result line says which lanes are still winding down.
 */
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { queueLanesApi, type LaneCount, type LaneResizeResult } from "../lib/queueLanesApi";
import { describeError, toastActionFailed } from "../state/toastStore";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

function describeResize(result: LaneResizeResult): string {
  const parts = [`${result.lane_count} lane${result.lane_count === 1 ? "" : "s"}.`];
  if (result.retiring_lanes.length) {
    parts.push(
      `Lane ${result.retiring_lanes.join(", ")} finishes its current ticket, then retires.`,
    );
  }
  if (result.moved_entries) {
    parts.push(`${result.moved_entries} waiting ticket(s) moved to the remaining lanes.`);
  }
  return parts.join(" ");
}

export function QueueLaneCount() {
  const [current, setCurrent] = useState<LaneCount | null>(null);
  const [draft, setDraft] = useState("");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");

  const load = useCallback(async () => {
    try {
      const count = await queueLanesApi.laneCount();
      setCurrent(count);
      setDraft(String(count.lane_count));
      setLoadError("");
    } catch (err) {
      setLoadError(describeError(err, "Could not read the lane count"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const parsed = Number(draft);
  const valid =
    current !== null &&
    Number.isInteger(parsed) &&
    parsed >= current.min &&
    parsed <= current.max;
  const changed = current !== null && parsed !== current.lane_count;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!valid || !changed || saving) return;
    setSaving(true);
    try {
      const resized = await queueLanesApi.setLaneCount(parsed);
      setCurrent((prev) => (prev ? { ...prev, lane_count: resized.lane_count } : prev));
      setResult(describeResize(resized));
    } catch (err) {
      toastActionFailed("Change lane count", err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="queue-rail-heading">Lanes</div>
      {loadError ? (
        <div className="queue-lane-count-error">
          <p className="queue-rail-empty">{loadError}</p>
          <Button variant="secondary" compact onClick={() => void load()}>
            Retry
          </Button>
        </div>
      ) : current === null ? (
        <p className="queue-rail-empty">Reading the lane count…</p>
      ) : (
        <form className="queue-lane-count-form" onSubmit={(event) => void save(event)}>
          <label htmlFor="queue-lane-count-input" className="queue-lane-count-label">
            Run at once
          </label>
          <Input
            id="queue-lane-count-input"
            type="number"
            min={current.min}
            max={current.max}
            step={1}
            value={draft}
            disabled={saving}
            aria-describedby="queue-lane-count-hint"
            onChange={(event) => {
              setDraft(event.target.value);
              setResult("");
            }}
          />
          <Button variant="primary" compact type="submit" disabled={!valid || !changed || saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <p id="queue-lane-count-hint" className="queue-rail-empty queue-lane-count-hint">
            {result ||
              (valid
                ? `Between ${current.min} and ${current.max}. Lowering it lets busy lanes finish first.`
                : `Enter a whole number from ${current.min} to ${current.max}.`)}
          </p>
        </form>
      )}
    </>
  );
}
