/**
 * Words and conversions for the autopilot's timer and schedule, kept out of
 * the components so the initiative page, the parent card and tests say the
 * same thing.
 */

import type { AutopilotStop, AutopilotView, AutopilotWindow } from "../api/initiativeApi";
import { formatWhen } from "./scheduleFormat";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** Durations the timer offers, in minutes. */
export const RUN_FOR_CHOICES = [30, 60, 120, 240, 480, 720, 1440];

export function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours && rest) return `${hours}h ${rest}m`;
  return hours ? `${hours}h` : `${rest}m`;
}

/** This browser's IANA zone: the zone a new window is drawn in. */
export function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** "HH:MM", whatever precision the server answered with. */
export function hhmm(value: string): string {
  return value.slice(0, 5);
}

export function formatDays(days: number[]): string {
  const sorted = [...days].sort((a, b) => a - b);
  if (sorted.length === 7) return "Every day";
  if (sorted.join() === "0,1,2,3,4") return "Mon–Fri";
  if (sorted.join() === "5,6") return "Weekends";
  return sorted.map((d) => WEEKDAYS[d]).join(", ");
}

export function formatWindow(window: AutopilotWindow, browserZone: string = localTimeZone()): string {
  const zone = window.timezone === browserZone ? "" : ` (${window.timezone})`;
  return `${formatDays(window.days)} ${hhmm(window.start)}–${hhmm(window.end)}${zone}`;
}

/** A `datetime-local` value ("2026-11-05T22:00", browser time) as an ISO instant. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** An ISO instant as a `datetime-local` value in browser time. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function describeStop(stop: AutopilotStop): string {
  return stop.kind === "deadline" ? `Stops ${formatWhen(stop.at)}` : `Runs ${formatDuration(stop.minutes)} each time`;
}

/**
 * One line naming what will start or stop it on its own, or null when nothing
 * will — the card then says nothing about timing at all.
 */
export function describeTiming(view: AutopilotView, browserZone: string = localTimeZone()): string | null {
  const parts: string[] = [];
  if (view.enabled && view.stops_at) parts.push(`Stops ${formatWhen(view.stops_at)}`);
  else if (!view.enabled) parts.push(...view.stops.map(describeStop));
  const { schedule } = view;
  if (schedule.window) parts.push(formatWindow(schedule.window, browserZone));
  if (!view.enabled && view.next_start) parts.push(`next start ${formatWhen(view.next_start)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
