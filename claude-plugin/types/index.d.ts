export type GardenTab = 'tend' | 'harvest' | 'weather'

/** A pending approval: a person's sign-off a run is waiting on. */
export type Approval = {
  id: string
  title: string
  level: string
  workspace: string
  stage: string
  ticket: string
  createdAt: string
}

/** A blocked ticket. */
export type Blocked = {
  id: string
  externalId: string
  title: string
  workspace: string
  kind: string
  type: string
}

/** A merge-queue entry. */
export type QueueEntry = {
  id: string
  workspace: string
  pr: number
  url: string
  state: string
  position: number
  wait: string
  title: string
  changedAt: string
}

/** A capacity lease, held or waiting. */
export type Lease = {
  id: string
  label: string
  footprint: string
  cpus: number
  memoryMb: number
  heldSeconds: number
  position: number | null
  waitSeconds: number | null
}

export type Capacity = {
  cpusUsed: number
  cpusCeiling: number
  memoryUsedMb: number
  memoryCeilingMb: number
  holders: Lease[]
  waiting: Lease[]
}

/**
 * What has been read. The light sources (approvals, counts, merge queue) are
 * read on every poll; the heavy ones (the blocked list, 5 s on the live
 * database, and the capacity probe, 2.5 s) only while the panel is in use, so
 * `blocked` and `capacity` are null until first read. A source that failed is
 * named in `failed` and keeps its last value.
 */
export type Garden = {
  lightAt: number
  heavyAt: number
  approvals: Approval[]
  blockedCount: number
  queue: QueueEntry[]
  blocked: Blocked[] | null
  capacity: Capacity | null
  failed: string[]
}

/** What the last read did: `error` names why it failed, `inFlight` stops a second one. */
export type Fetch = { inFlight: boolean; error: string }

declare module 'claude-code' {
  interface PluginState {
    loregarden: {
      tab: GardenTab
      garden: Shaped<Garden | null>
      fetch: Fetch
      /** When the person last opened or used the panel: heavy reads run within 10 minutes of it. */
      activeAt: number
    }
  }
}
