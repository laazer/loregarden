/**
 * What on this machine needs a person, worst first.
 *
 * The Machine page used to answer this in three places: an Attention tab in
 * the rail (orphaned and unverified leases), caveats under the rail's Capacity
 * tab (a ceiling nobody measured), and nowhere at all for the two things that
 * actually needed someone when the page was surveyed — a disk at 96% and a
 * lease held for 53.8 hours. This is the one list all of them land in.
 *
 * Each rule sits where the answer changes for the operator, not at a round
 * number: past 90% the disk is a cleanup away from failing builds and worktree
 * checkouts. Of 4,596 finished leases measured in October 2026, 6 ran past an
 * hour and one past 12, and that one ended by expiring rather than by a release,
 * so a holder past 12 hours is a process that has hung or lost its release.
 */

import type {
  CapacityPool,
  DockerCapacityStatus,
  DockerCeiling,
  DockerLeaseRow,
  DockerUnverifiable,
} from "../api/dockerTypes";
import type { DiskVolume } from "../api/diskTypes";

export const DISK_FULL_AT = 0.9;
export const LONG_HELD_SECONDS = 12 * 60 * 60;

export type AttentionTone = "bad" | "warn" | "info";

export type AttentionItem =
  | { kind: "disk"; id: string; tone: AttentionTone; volume: DiskVolume }
  | { kind: "long_held"; id: string; tone: AttentionTone; lease: DockerLeaseRow }
  | { kind: "orphaned"; id: string; tone: AttentionTone; lease: DockerLeaseRow }
  | { kind: "unverified"; id: string; tone: AttentionTone; entry: DockerUnverifiable }
  | { kind: "ceiling"; id: string; tone: AttentionTone; text: string };

interface PoolWords {
  stale: string;
  unknown: string;
  override: string;
}

/** How each pool's ceiling is described when it is not a fresh measurement. */
const POOL_WORDS: Record<CapacityPool, PoolWords> = {
  host: {
    stale: "This machine's size could not be read",
    unknown: "This machine's size has never been read, so every reservation is being refused",
    override: "Machine ceiling set by configuration rather than measured.",
  },
  docker: {
    stale: "Docker is not answering",
    unknown:
      "Docker capacity has never been measured, so docker reservations are being refused",
    override: "Docker ceiling set by configuration rather than measured.",
  },
};

/** The one line worth saying about a ceiling that is not a fresh measurement. */
function ceilingItem(pool: CapacityPool, ceiling: DockerCeiling): AttentionItem | null {
  const words = POOL_WORDS[pool];
  const reason = ceiling.error ? ` — ${ceiling.error}` : "";
  const id = `ceiling-${pool}`;
  if (ceiling.source === "stale_probe") {
    return {
      kind: "ceiling",
      id,
      tone: "warn",
      text: `${words.stale}. Showing the last good measurement${reason}`,
    };
  }
  if (ceiling.source === "unknown") {
    return { kind: "ceiling", id, tone: "bad", text: `${words.unknown}${reason}` };
  }
  if (ceiling.source === "config_override") {
    return { kind: "ceiling", id, tone: "info", text: words.override };
  }
  return null;
}

export function diskUsedFraction(volume: DiskVolume): number {
  return volume.total_bytes > 0 ? volume.used_bytes / volume.total_bytes : 0;
}

const TONE_ORDER: Record<AttentionTone, number> = { bad: 0, warn: 1, info: 2 };

/**
 * Every item, worst first. Either read may be missing (still loading, or it
 * failed, which the page reports on its own); the other still contributes.
 */
export function machineAttention(
  status: DockerCapacityStatus | null,
  volume: DiskVolume | null,
): AttentionItem[] {
  const items: AttentionItem[] = [];

  if (volume && diskUsedFraction(volume) > DISK_FULL_AT) {
    items.push({ kind: "disk", id: "disk", tone: "bad", volume });
  }

  if (status?.enabled) {
    for (const lease of status.holders) {
      if (lease.status === "orphaned") {
        items.push({ kind: "orphaned", id: `orphaned-${lease.lease_id}`, tone: "bad", lease });
      } else if ((lease.held_seconds ?? 0) > LONG_HELD_SECONDS) {
        items.push({ kind: "long_held", id: `held-${lease.lease_id}`, tone: "warn", lease });
      }
    }
    for (const entry of status.unverifiable) {
      items.push({ kind: "unverified", id: `unverified-${entry.lease_id}`, tone: "bad", entry });
    }
    for (const item of [
      ceilingItem("host", status.host.ceiling),
      ceilingItem("docker", status.ceiling),
    ]) {
      if (item) items.push(item);
    }
  }

  // Stable sort: within a tone, the order above (disk, leases, ceilings) holds.
  return items.sort((a, b) => TONE_ORDER[a.tone] - TONE_ORDER[b.tone]);
}
