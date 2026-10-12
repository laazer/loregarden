/**
 * Reasons for ignoring an approval: the presets the Ignore dialog offers, and
 * the one it can write for you from what the card already knows.
 *
 * Deterministic on purpose. The facts that make a card ignorable (its ticket is
 * finished, another card says the same thing, it has sat for days) are all on
 * the client already, and a sentence built from them is reviewable before it is
 * sent, which a model's guess would not make any truer.
 */
import type { Approval } from "../api/client";

export interface IgnorePreset {
  key: "ticket_completed" | "duplicate" | "test" | "superseded";
  label: string;
}

/** A ticket in one of these states has nothing left to decide. */
const FINISHED_STATES = new Set(["done", "wont_do"]);

/** Days a card can sit before the suggestion calls it out of date. */
export const STALE_AFTER_DAYS = 3;

export function ticketIsFinished(approval: Approval): boolean {
  return FINISHED_STATES.has(approval.ticket_state ?? "");
}

/** The presets for this card. "Ticket completed" only when it is. */
export function ignorePresets(approval: Approval): IgnorePreset[] {
  const presets: IgnorePreset[] = [];
  if (ticketIsFinished(approval)) presets.push({ key: "ticket_completed", label: "Ticket completed" });
  presets.push(
    { key: "duplicate", label: "Duplicate" },
    { key: "test", label: "Test approval" },
    { key: "superseded", label: "Superseded or out of date" },
  );
  return presets;
}

function ageInDays(approval: Approval, now: Date): number | null {
  if (!approval.created_at) return null;
  const created = new Date(approval.created_at);
  if (Number.isNaN(created.getTime())) return null;
  return Math.floor((now.getTime() - created.getTime()) / 86_400_000);
}

/**
 * A reason written from the card's own facts, strongest first: a finished
 * ticket, then a duplicate in the same list, then age. Falls back to naming the
 * card, so the result is never empty.
 */
export function suggestIgnoreReason(
  approval: Approval,
  { hasDuplicate, now = new Date() }: { hasDuplicate: boolean; now?: Date },
): string {
  const ticket = approval.ticket_external_id || "the ticket";
  if (ticketIsFinished(approval)) {
    const state = approval.ticket_state === "wont_do" ? "closed as won't do" : "done";
    return `Ticket completed: ${ticket} is ${state}, so "${approval.title}" no longer needs a decision.`;
  }
  if (hasDuplicate) {
    return `Duplicate: another pending card on ${ticket} asks the same thing ("${approval.title}").`;
  }
  const days = ageInDays(approval, now);
  if (days !== null && days >= STALE_AFTER_DAYS) {
    return `Out of date: "${approval.title}" has waited ${days} days and no longer reflects ${ticket}.`;
  }
  return `Superseded: "${approval.title}" on ${ticket} no longer needs a decision.`;
}

/** Ids of pending approvals that share a ticket and title with another one. */
export function duplicateApprovalIds(approvals: Approval[]): Set<string> {
  const byKey = new Map<string, string[]>();
  for (const approval of approvals) {
    const key = `${approval.ticket_id}\u0000${approval.title}`;
    byKey.set(key, [...(byKey.get(key) ?? []), approval.id]);
  }
  return new Set([...byKey.values()].filter((ids) => ids.length > 1).flat());
}
