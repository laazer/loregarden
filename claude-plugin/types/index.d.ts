/** A pending approval: a person's sign-off a run is waiting on. */
export type Approval = {
  id: string
  /** What it asks, in the words its card uses: its title, else its tool, else its kind. */
  label: string
  kind: string
  workspace: string
  ticketId: string
  ticketExternalId: string
}

/** A ticket Home reads: in progress, blocked or parked. */
export type Ticket = {
  id: string
  externalId: string
  title: string
  workspace: string
  state: string
  blockedOn: string
  /** Each workflow stage's status, in order: the running card's strip. */
  stages: { name: string; status: string }[]
}

/** A recent agent run. */
export type Run = {
  id: string
  ticketId: string
  ticketTitle: string
  ticketExternalId: string
  status: string
  agent: string
  stage: string
  /** When it last did something: finished, else started, else created. */
  at: string
}

/** A done ticket whose ship is not green. */
export type Ship = {
  ticketId: string
  externalId: string
  title: string
  reason: string
  pr: number | null
  checks: string[]
  branch: string
}

/** The ticket counts Home's header reads. */
export type Counts = { running: number; inProgress: number; blocked: number }

/** A merge-queue entry. */
export type QueueEntry = {
  id: string
  ticketId: string
  workspace: string
  pr: number
  url: string
  state: string
  position: number
  wait: string
  title: string
  changedAt: string
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

/** What a held command last said about itself, and when. */
export type Progress = { summary: string; reportedAt: string }

/** A capacity lease, held or waiting. */
export type Lease = {
  id: string
  /** What the holder said it is running, e.g. "git push (pre-push client+server suites)". */
  what: string
  /** "worktree @ branch", or the rest of the holder's label when it gave no parts. */
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

export type Capacity = {
  cpusUsed: number
  cpusCeiling: number
  memoryUsedMb: number
  memoryCeilingMb: number
  holders: Lease[]
  waiting: Lease[]
}

/**
 * What has been read: Home's sources, minus Baxter. The light ones are read on
 * every poll; the heavy ones (tickets, recent runs and the capacity probe,
 * about 3 s each on the live database) only while the panel is in use, so they
 * are null until first read. A source that failed is named in `failed` and
 * keeps its last value.
 */
export type Garden = {
  lightAt: number
  heavyAt: number
  approvals: Approval[]
  counts: Counts | null
  ships: Ship[]
  queue: QueueEntry[]
  lanes: Lane[]
  laneQueue: LaneWait[]
  laneTotal: number | null
  /** Names for lane and capacity cards whose ticket is not in `tickets`, by ticket id. */
  names: Record<string, TicketName>
  tickets: Ticket[] | null
  runs: Run[] | null
  capacity: Capacity | null
  failed: string[]
}

/** What the last read did: `error` names why it failed, `inFlight` stops a second one. */
export type Fetch = { inFlight: boolean; error: string }

declare module 'claude-code' {
  interface PluginState {
    loregarden: {
      garden: Shaped<Garden | null>
      fetch: Fetch
      /** Needs you lists every row, not the first ten (Home's "Show all"). */
      showAll: boolean
      /** When the person last opened or used the panel: heavy reads run within 10 minutes of it. */
      activeAt: number
    }
  }
}
