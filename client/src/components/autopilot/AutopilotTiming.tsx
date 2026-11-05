import { useId, useState } from "react";

import type { AutopilotChange, AutopilotStop, AutopilotView, AutopilotWindow } from "../../api/initiativeApi";
import {
  RUN_FOR_CHOICES,
  WEEKDAYS,
  describeTiming,
  formatDuration,
  hhmm,
  isoToLocalInput,
  localInputToIso,
  localTimeZone,
} from "../../lib/autopilotTiming";
import { describeError } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Select } from "../ui/Select";
import "./AutopilotTiming.css";

type StopMode = "none" | "for" | "until";

interface Draft {
  stopMode: StopMode;
  forMinutes: number;
  until: string;
  startOnce: boolean;
  startAt: string;
  repeat: boolean;
  days: number[];
  windowStart: string;
  windowEnd: string;
  timezone: string;
}

function draftFrom(view: AutopilotView): Draft {
  const deadline = view.stops.find((s) => s.kind === "deadline");
  const runFor = view.stops.find((s) => s.kind === "run_for");
  const window = view.schedule.window ?? null;
  return {
    stopMode: deadline ? "until" : runFor ? "for" : "none",
    forMinutes: runFor?.minutes ?? 120,
    until: isoToLocalInput(deadline?.at),
    startOnce: Boolean(view.schedule.start_at),
    startAt: isoToLocalInput(view.schedule.start_at),
    repeat: window !== null,
    days: window?.days ?? [0, 1, 2, 3, 4],
    windowStart: window ? hhmm(window.start) : "22:00",
    windowEnd: window ? hhmm(window.end) : "06:00",
    timezone: window?.timezone ?? localTimeZone(),
  };
}

/** The change the draft asks for, or why it cannot be saved. */
function changeFrom(draft: Draft, now: number): { change: AutopilotChange } | { problem: string } {
  const stops: AutopilotStop[] = [];
  if (draft.stopMode === "for") stops.push({ kind: "run_for", minutes: draft.forMinutes });
  if (draft.stopMode === "until") {
    const at = localInputToIso(draft.until);
    if (!at) return { problem: "Pick when it should stop." };
    if (Date.parse(at) <= now) return { problem: "The stop time is already past." };
    stops.push({ kind: "deadline", at });
  }
  let startAt: string | null = null;
  if (draft.startOnce) {
    startAt = localInputToIso(draft.startAt);
    if (!startAt) return { problem: "Pick when it should start." };
    if (Date.parse(startAt) <= now) return { problem: "The start time is already past." };
  }
  let window: AutopilotWindow | null = null;
  if (draft.repeat) {
    if (draft.days.length === 0) return { problem: "Pick at least one day for the weekly window." };
    if (!draft.windowStart || !draft.windowEnd) return { problem: "Pick when the weekly window opens and closes." };
    if (draft.windowStart === draft.windowEnd) return { problem: "The weekly window opens and closes at the same time." };
    window = { days: draft.days, start: draft.windowStart, end: draft.windowEnd, timezone: draft.timezone };
  }
  return { change: { stops, schedule: { start_at: startAt, window } } };
}

/** "Stops Nov 6, 07:00 · Mon–Fri 22:00–06:00", under the card's status; nothing when no timing is set. */
export function AutopilotTimingSummary({ view, className }: { view: AutopilotView; className?: string }) {
  const line = describeTiming(view);
  return line ? <p className={className}>Timer: {line}</p> : null;
}

/**
 * The autopilot's "Timer & schedule" button and the dialog it opens.
 *
 * Answers "when does this run without me — when does it stop, and when does it
 * start?". The dialog sets a stop (a duration, or a clock time), a one-off
 * start, and a weekly window; Save stores them, and while the autopilot is off
 * "Save and turn on" also starts it, so a timer can start counting now.
 */
export function AutopilotTimingButton({
  view,
  busy,
  onSave,
}: {
  view: AutopilotView;
  busy: boolean;
  onSave: (change: AutopilotChange) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(view));
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const id = useId();
  const titleId = `${id}-title`;
  const set = (patch: Partial<Draft>) => {
    setFailure(null);
    setDraft((d) => ({ ...d, ...patch }));
  };

  const close = () => setOpen(false);
  const checked = changeFrom(draft, Date.now());
  const problem = "problem" in checked ? checked.problem : null;

  const save = async (turnOn: boolean) => {
    if ("problem" in checked || saving) return;
    setSaving(true);
    try {
      await onSave(turnOn ? { ...checked.change, enabled: true } : checked.change);
      setOpen(false);
    } catch (error) {
      // Kept open with the fields as they were; the reason is shown inline.
      setFailure(describeError(error, "the server refused the timer"));
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (day: number) =>
    set({ days: draft.days.includes(day) ? draft.days.filter((d) => d !== day) : [...draft.days, day] });

  return (
    <>
      <Button
        variant="secondary"
        compact
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={busy}
        title="Set when the autopilot stops and starts on its own"
        onClick={() => {
          setDraft(draftFrom(view));
          setFailure(null);
          setOpen(true);
        }}
      >
        Timer &amp; schedule
      </Button>
      <ModalShell open={open} onDismiss={saving ? undefined : close} labelledBy={titleId}>
        <div className="modal-header">
          <div>
            <div className="state-label">Autopilot</div>
            <h2 id={titleId} className="modal-title">
              Timer &amp; schedule
            </h2>
            <p className="modal-subtitle">When it stops and starts without you. Off means finish what is running.</p>
          </div>
          <IconCloseButton disabled={saving} onClick={close} />
        </div>

        <div className="modal-body">
          <fieldset className="autopilot-timing-group" disabled={saving}>
            <legend className="modal-section-title">Stop on its own</legend>
            <label className="autopilot-timing-option">
              <Input
                type="radio"
                name={`${id}-stop`}
                checked={draft.stopMode === "none"}
                onChange={() => set({ stopMode: "none" })}
              />
              No timer: runs until turned off
            </label>
            <label className="autopilot-timing-option">
              <Input
                type="radio"
                name={`${id}-stop`}
                checked={draft.stopMode === "for"}
                onChange={() => set({ stopMode: "for" })}
              />
              Run for
            </label>
            {draft.stopMode === "for" ? (
              <div className="autopilot-timing-indent">
                <Select
                  aria-label="How long each run lasts"
                  value={String(draft.forMinutes)}
                  onChange={(e) => set({ forMinutes: Number(e.target.value) })}
                >
                  {RUN_FOR_CHOICES.map((m) => (
                    <option key={m} value={m}>
                      {formatDuration(m)}
                    </option>
                  ))}
                </Select>
                <p className="modal-hint">Counted from each time it is turned on, by you or by the schedule.</p>
              </div>
            ) : null}
            <label className="autopilot-timing-option">
              <Input
                type="radio"
                name={`${id}-stop`}
                checked={draft.stopMode === "until"}
                onChange={() => set({ stopMode: "until" })}
              />
              Run until
            </label>
            {draft.stopMode === "until" ? (
              <div className="autopilot-timing-indent">
                <Input
                  type="datetime-local"
                  aria-label="When it stops"
                  value={draft.until}
                  onChange={(e) => set({ until: e.target.value })}
                />
                <p className="modal-hint">Once, in your local time. Cleared after it fires.</p>
              </div>
            ) : null}
          </fieldset>

          <fieldset className="autopilot-timing-group" disabled={saving}>
            <legend className="modal-section-title">Start on its own</legend>
            <label className="autopilot-timing-option">
              <Input
                type="checkbox"
                checked={draft.startOnce}
                onChange={(e) => set({ startOnce: e.target.checked })}
              />
              Once at
            </label>
            {draft.startOnce ? (
              <div className="autopilot-timing-indent">
                <Input
                  type="datetime-local"
                  aria-label="When it starts"
                  value={draft.startAt}
                  onChange={(e) => set({ startAt: e.target.value })}
                />
              </div>
            ) : null}
            <label className="autopilot-timing-option">
              <Input type="checkbox" checked={draft.repeat} onChange={(e) => set({ repeat: e.target.checked })} />
              Every week, in a window
            </label>
            {draft.repeat ? (
              <div className="autopilot-timing-indent">
                <div className="autopilot-timing-days" role="group" aria-label="Days the window opens">
                  {WEEKDAYS.map((name, day) => (
                    <label key={name} className="autopilot-timing-day">
                      <Input type="checkbox" checked={draft.days.includes(day)} onChange={() => toggleDay(day)} />
                      {name}
                    </label>
                  ))}
                </div>
                <div className="autopilot-timing-times">
                  <label className="modal-field">
                    <span className="modal-field-label">Opens</span>
                    <Input
                      type="time"
                      value={draft.windowStart}
                      onChange={(e) => set({ windowStart: e.target.value })}
                    />
                  </label>
                  <label className="modal-field">
                    <span className="modal-field-label">Closes</span>
                    <Input type="time" value={draft.windowEnd} onChange={(e) => set({ windowEnd: e.target.value })} />
                  </label>
                </div>
                <p className="modal-hint">
                  In {draft.timezone}. A close at or before the open is the next morning. It turns itself off when a
                  window it opened closes; a run you started keeps going.
                </p>
              </div>
            ) : null}
          </fieldset>

          {failure ?? problem ? (
            <p className="form-error" role="alert">
              {failure ?? problem}
            </p>
          ) : null}
        </div>

        <div className="modal-footer">
          <Button variant="secondary" disabled={saving} onClick={close}>
            Cancel
          </Button>
          {!view.enabled && view.available ? (
            <Button variant="secondary" disabled={saving || problem !== null} onClick={() => void save(true)}>
              Save and turn on
            </Button>
          ) : null}
          <Button variant="primary" disabled={saving || problem !== null} onClick={() => void save(false)}>
            {saving ? "Saving…" : "Save timer"}
          </Button>
        </div>
      </ModalShell>
    </>
  );
}
