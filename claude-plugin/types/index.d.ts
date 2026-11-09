export type GardenTab = 'tend' | 'busy' | 'harvest'

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

/** What a held command last said about itself, and when. */
export type Progress = { summary: string; reportedAt: string }

/** A capacity lease, held or waiting. */
export type Lease = {
  id: string
  /** What the holder said it is running, e.g. "git push (pre-push client+server suites)". */
  what: string
  /** "worktree @ branch", or the holder's whole label when it gave no parts. */
  where: string
  pid: number | null
  footprint: string
  cpus: number
  memoryMb: number
  heldSeconds: number
  position: number | null
  waitSeconds: number | null
  /** Set when the lease belongs to a ticket's run; it is then drawn on that lane's card. */
  ticketId: string
  runId: string
  progress: Progress | null
  /** A waiter that stopped asking: it will be dropped from the line. */
  pollStalled: boolean
}

/** A lane at work: a whole ticket's orchestration, or one agent run. */
export type Lane = {
  slot: number
  ticketId: string
  runId: string
  orchestration: boolean
  agent: string
  stage: string
  status: string
  elapsedSeconds: number
}

/** A ticket or stage waiting for a lane. */
export type LaneWait = {
  id: string
  ticketId: string
  slot: number
  position: number
  orchestration: boolean
  agent: string
  stage: string
  waitSeconds: number
}

/** The ticket a card names, read once per ticket and kept. */
export type TicketName = { externalId: string; title: string; workspace: string }

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
  /** Lanes at work and the line for them; light reads, 0.1 s on the live database. */
  lanes: Lane[]
  laneQueue: LaneWait[]
  /** Names for the tickets the lane and capacity cards point at, by ticket id. */
  tickets: Record<string, TicketName>
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
