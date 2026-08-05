/**
 * Which lane to run in, with what each lane is doing right now.
 *
 * Every way of starting a ticket now takes an execution slot, so every dialog
 * that starts one has to answer "where?". The answer can be left to the pool —
 * Auto is the default and takes the quietest lane — but the choice is offered
 * with the state behind it, because "start it in the lane that is about to
 * free" is a judgement the board can inform and the server cannot make.
 *
 * A full pool never refuses. Picking a busy lane means waiting behind what is
 * in it, which the copy says outright rather than leaving to be discovered.
 */

import { useQuery } from '@tanstack/react-query';

import { API_BASE } from '../api/client';
import type { QueueLane } from '../lib/queueSocket';
import './LanePicker.css';

/** Null means "no preference" — the server picks the quietest lane. */
export type LaneChoice = number | null;

async function fetchLanes(): Promise<QueueLane[]> {
  const response = await fetch(`${API_BASE}/api/parallel/status`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  return (data.lanes ?? []) as QueueLane[];
}

function laneSummary(lane: QueueLane): string {
  const waiting = lane.waiting.length;
  const behind = waiting === 1 ? '1 waiting' : `${waiting} waiting`;
  if (!lane.running) return waiting ? `Idle · ${behind}` : 'Idle';
  const running = lane.running.ticket_code || lane.running.ticket_title || 'a ticket';
  return waiting ? `Running ${running} · ${behind}` : `Running ${running}`;
}

interface LanePickerProps {
  value: LaneChoice;
  onChange: (value: LaneChoice) => void;
  /** Off while the dialog is closed, so a shut modal stops polling. */
  enabled?: boolean;
  disabled?: boolean;
}

export function LanePicker({ value, onChange, enabled = true, disabled }: LanePickerProps) {
  // Polled rather than socketed: a dialog is open for seconds, and the queue's
  // own socket lives on the queue screen, which is not mounted behind most of
  // the places this is used.
  const lanes = useQuery({
    queryKey: ['queue-lanes-picker'],
    queryFn: fetchLanes,
    enabled,
    refetchInterval: 5000,
  });
  const rows = lanes.data ?? [];
  const chosen = rows.find((lane) => lane.slot_number === value);

  return (
    <div className="lane-picker">
      <div className="lane-picker-label" id="lane-picker-label">
        Execution lane
      </div>
      <div className="lane-picker-options" role="radiogroup" aria-labelledby="lane-picker-label">
        <button
          type="button"
          role="radio"
          aria-checked={value === null}
          className={`lane-picker-option${value === null ? ' is-selected' : ''}`}
          disabled={disabled}
          onClick={() => onChange(null)}
        >
          <span className="lane-picker-option-name">Auto</span>
          <span className="lane-picker-option-sub">Quietest lane</span>
        </button>
        {rows.map((lane) => (
          <button
            key={lane.slot_number}
            type="button"
            role="radio"
            aria-checked={value === lane.slot_number}
            className={`lane-picker-option${value === lane.slot_number ? ' is-selected' : ''}${
              lane.running ? ' is-busy' : ''
            }`}
            disabled={disabled}
            onClick={() => onChange(lane.slot_number)}
          >
            <span className="lane-picker-option-name">Lane {lane.slot_number}</span>
            <span className="lane-picker-option-sub">{laneSummary(lane)}</span>
          </button>
        ))}
      </div>
      <div className="lane-picker-hint">
        {lanes.isError
          ? 'Could not read the queue — this will still start in whichever lane is free.'
          : chosen?.running
            ? `Starts when lane ${chosen.slot_number} finishes what it is running.`
            : 'Starts now if a lane is free, otherwise waits its turn in one.'}
      </div>
    </div>
  );
}
