import type { Approval, Blocked, Capacity, Garden, Lane, LaneWait, Lease, QueueEntry, TicketName } from '../types'

/** The server's JSON, read field by field: a missing field becomes a neutral value. */
type Json = Record<string, unknown>

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const numOrNull = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null
const list = (v: unknown): Json[] => (Array.isArray(v) ? (v as Json[]) : [])
const obj = (v: unknown): Json => (v && typeof v === 'object' ? (v as Json) : {})

export function parseApprovals(body: unknown): Approval[] {
  return list(body).map(a => ({
    id: str(a.id),
    title: str(a.title) || 'Untitled approval',
    level: str(a.level),
    workspace: str(a.workspace_slug) || 'no workspace',
    stage: str(a.stage_name),
    ticket: str(a.ticket_external_id),
    createdAt: str(a.created_at),
  }))
}

export function parseBlocked(body: unknown): Blocked[] {
  return list(body).map(t => ({
    id: str(t.id),
    externalId: str(t.external_id),
    title: str(t.title) || 'Untitled ticket',
    workspace: str(t.workspace_slug) || 'no workspace',
    kind: str(t.block_kind),
    type: str(t.work_item_type),
  }))
}

export function parseQueue(body: unknown): QueueEntry[] {
  return list(obj(body).queues).flatMap(q =>
    list(q.entries).map(e => ({
      id: str(e.id),
      workspace: str(q.workspace_slug),
      pr: num(e.pr_number),
      url: str(e.pr_url),
      state: str(e.state),
      position: num(e.position),
      wait: str(e.wait),
      title: str(e.ticket_title) || str(e.source_branch),
      changedAt: str(e.state_changed_at),
    })),
  )
}

function parseLease(l: Json): Lease {
  const holder = obj(l.holder)
  const label = str(l.holder_label)
  const place = [str(holder.worktree), str(holder.branch)].filter(Boolean).join(' @ ')
  const progress = obj(l.progress)
  return {
    id: str(l.lease_id),
    what: str(holder.what) || label.split(' · ')[0] || 'unnamed holder',
    where: place || (str(holder.what) ? '' : label.split(' · ').slice(1).join(' · ')),
    pid: numOrNull(holder.pid),
    footprint: str(l.footprint),
    cpus: num(l.cpus),
    memoryMb: num(l.memory_mb),
    heldSeconds: num(l.held_seconds),
    position: numOrNull(l.position),
    waitSeconds: numOrNull(l.estimated_wait_seconds),
    ticketId: str(l.ticket_id),
    runId: str(l.agent_run_id),
    progress: str(progress.summary)
      ? { summary: str(progress.summary), reportedAt: str(progress.reported_at) }
      : null,
    pollStalled: l.poll_stalled === true,
  }
}

export function parseLanes(body: unknown): Lane[] {
  return list(obj(body).active_runs)
    .map(r => ({
      slot: num(r.slot_number),
      ticketId: str(r.ticket_id),
      runId: str(r.run_id),
      orchestration: str(r.orchestration_run_id) !== '',
      agent: str(r.agent_id),
      stage: str(r.stage_key),
      status: str(r.status),
      elapsedSeconds: num(r.elapsed_seconds),
    }))
    .sort((a, b) => a.slot - b.slot)
}

export function parseLaneQueue(body: unknown): LaneWait[] {
  return list(obj(body).queued_runs).map(q => ({
    id: str(q.entry_id) || str(q.run_id),
    ticketId: str(q.ticket_id),
    slot: num(q.slot_number),
    position: num(q.position),
    orchestration: str(q.entry_kind) === 'orchestration',
    agent: str(q.agent_id),
    stage: str(q.stage_key),
    waitSeconds: num(q.wait_seconds),
  }))
}

export function parseTicketName(body: unknown): TicketName {
  const t = obj(body)
  return {
    externalId: str(t.external_id),
    title: str(t.title) || 'Untitled ticket',
    workspace: str(t.workspace_slug),
  }
}

/** The lease a lane's run holds, if any: by run, else by ticket. */
export const leaseFor = (lane: Lane, holders: Lease[]): Lease | undefined =>
  holders.find(h => h.runId !== '' && h.runId === lane.runId) ??
  holders.find(h => h.ticketId !== '' && h.ticketId === lane.ticketId)

/** A held command that has not reported progress for this long is drawn as possibly hung. */
export const QUIET_SECONDS = 15 * 60

/** Seconds since the holder last reported progress, or null when it never reports any. */
export function quietSeconds(lease: Lease, now: number): number | null {
  if (!lease.progress?.reportedAt) return null
  const at = parseTime(lease.progress.reportedAt)
  return at === null ? null : Math.max(0, (now - at) / 1000)
}

/** The host pool: what every repo on this machine queues against. */
export function parseCapacity(body: unknown): Capacity {
  const host = obj(obj(body).host)
  const holders = list(obj(body).holders).map(parseLease)
  return {
    cpusUsed: num(obj(host.in_use).cpus),
    cpusCeiling: num(obj(host.ceiling).cpus),
    memoryUsedMb: num(obj(host.in_use).memory_mb),
    memoryCeilingMb: num(obj(host.ceiling).memory_mb),
    holders,
    waiting: list(obj(body).waiting).map(parseLease),
  }
}

/** Entries still on their way in; `merged` and `withdrawn` are history. */
export const isLanding = (e: QueueEntry): boolean => e.state !== 'merged' && e.state !== 'withdrawn'

type Fetcher = (url: string) => Promise<{ ok: boolean; status: number; text: string }>

/** Read on every poll: about a second of server time together on the live database. */
export const LIGHT = {
  approvals: '/api/inbox/approvals',
  counts: '/api/tickets/status-summary',
  queue: '/api/merge-queue',
  lanes: '/api/parallel/active-runs',
  laneQueue: '/api/parallel/queued-runs',
} as const

/** Read only while the panel is in use: 5 s and 2.5 s on the live database. */
export const HEAVY = {
  blocked: '/api/tickets?state=blocked&limit=500',
  capacity: '/api/docker/capacity',
} as const

type Source = keyof typeof LIGHT | keyof typeof HEAVY

async function readJson(fetcher: Fetcher, url: string): Promise<unknown> {
  const res = await fetcher(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return JSON.parse(res.text)
}

/**
 * Reads the light sources, and the heavy ones when `heavy`, all at once, on
 * top of what was read before. A source that fails is named in `failed` and
 * keeps its last value, so one broken endpoint does not blank the other tabs.
 * When every source asked fails, the server is unreachable: that throws.
 */
export async function readGarden(
  fetcher: Fetcher,
  base: string,
  now: number,
  previous: Garden | null,
  heavy: boolean,
): Promise<Garden> {
  const root = base.replace(/\/+$/, '')
  const paths: Partial<Record<Source, string>> = heavy ? { ...LIGHT, ...HEAVY } : { ...LIGHT }
  const names = Object.keys(paths) as Source[]
  const settled = await Promise.allSettled(names.map(n => readJson(fetcher, root + paths[n])))
  const results = new Map(names.map((n, i) => [n, settled[i]]))
  const failed: string[] = []
  /** The source's body; undefined when it was not asked or failed (and then it is named). */
  const value = (n: Source): unknown => {
    const r = results.get(n)
    if (!r) return undefined
    if (r.status === 'fulfilled') return r.value
    const reason: unknown = r.reason
    failed.push(`${n} (${reason instanceof Error ? reason.message : String(reason)})`)
    return undefined
  }
  const pick = <T>(n: Source, parse: (body: unknown) => T, last: T): T => {
    const body = value(n)
    return body === undefined ? last : parse(body)
  }
  // Every source is picked before the all-failed check: `failed` fills as they are.
  const approvals = pick('approvals', parseApprovals, previous?.approvals ?? [])
  const blockedCount = pick('counts', b => num(obj(b).blocked), previous?.blockedCount ?? 0)
  const queue = pick('queue', parseQueue, previous?.queue ?? [])
  const lanes = pick('lanes', parseLanes, previous?.lanes ?? [])
  const laneQueue = pick('laneQueue', parseLaneQueue, previous?.laneQueue ?? [])
  const blocked = pick('blocked', parseBlocked, previous?.blocked ?? null)
  const capacity = pick('capacity', parseCapacity, previous?.capacity ?? null)
  if (failed.length === names.length) {
    throw new Error(`Loregarden server at ${root} did not answer: ${failed.join('; ')}`)
  }
  const wanted = [
    ...lanes.map(l => l.ticketId),
    ...laneQueue.map(q => q.ticketId),
    ...(capacity ? [...capacity.holders, ...capacity.waiting].map(l => l.ticketId) : []),
  ].filter(Boolean)
  const tickets = await nameTickets(fetcher, root, wanted, previous?.tickets ?? {}, failed)
  return {
    lightAt: now,
    heavyAt: heavy ? now : (previous?.heavyAt ?? 0),
    approvals,
    blockedCount,
    queue,
    lanes,
    laneQueue,
    tickets,
    blocked,
    capacity,
    failed,
  }
}

/** Ticket names read per poll at most: a detail read is about 0.9 s on the live database. */
export const NAMES_PER_READ = 6

/**
 * Names for the tickets the cards point at: kept ones reused, new ones read
 * (a few per poll), and ones no card points at any more dropped. A failed read
 * is named in `failed`; its card shows the ticket id until a later poll names it.
 */
async function nameTickets(
  fetcher: Fetcher,
  root: string,
  wanted: string[],
  known: Record<string, TicketName>,
  failed: string[],
): Promise<Record<string, TicketName>> {
  const ids = [...new Set(wanted)]
  const missing = ids.filter(id => !(id in known)).slice(0, NAMES_PER_READ)
  const read = await Promise.allSettled(
    missing.map(id => readJson(fetcher, `${root}/api/tickets/${encodeURIComponent(id)}`)),
  )
  const names: Record<string, TicketName> = {}
  for (const id of ids) if (known[id]) names[id] = known[id]
  const errors: string[] = []
  read.forEach((r, i) => {
    if (r.status === 'fulfilled') names[missing[i]!] = parseTicketName(r.value)
    else errors.push(r.reason instanceof Error ? r.reason.message : String(r.reason))
  })
  if (errors.length) failed.push(`ticket names (${[...new Set(errors)].join(', ')})`)
  return names
}

/** Heavy reads run while the panel was used in the last ten minutes. */
export const ACTIVE_MS = 10 * 60_000
export const isActive = (activeAt: number, now: number): boolean => now - activeAt < ACTIVE_MS

/** Groups rows under their workspace, largest group first. */
export function byWorkspace<T extends { workspace: string }>(rows: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const row of rows) groups.set(row.workspace, [...(groups.get(row.workspace) ?? []), row])
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
}

/** "3d", "5h", "12m", "now": how long ago an ISO time was, or "" when it does not parse. */
export function ago(iso: string, now: number): string {
  const at = parseTime(iso)
  return at === null ? '' : span(Math.max(0, (now - at) / 1000))
}

/** An ISO time in ms, or null. The server writes naive UTC; a time with no zone is read as UTC, not local. */
function parseTime(iso: string): number | null {
  if (!iso) return null
  const at = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(at) ? null : at
}

/** "3d", "5h", "12m", "now" for a number of seconds. */
export function span(seconds: number): string {
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

/** A ten-cell bar of used against ceiling, `▰▰▰▱▱▱▱▱▱▱`. */
export function bar(used: number, ceiling: number): string {
  const filled = ceiling > 0 ? Math.min(10, Math.round((used / ceiling) * 10)) : 0
  return '▰'.repeat(filled) + '▱'.repeat(10 - filled)
}

/** The status-line text: one glance at what the garden needs. */
export function statusText(garden: Garden | null, error: string): string {
  if (error) return '🥀 Loregarden unreachable'
  if (!garden) return '🌱 Loregarden…'
  const landing = garden.queue.filter(isLanding).length
  const waiting = garden.capacity?.waiting.length ?? 0
  const parts = [`${garden.approvals.length} to tend`, `${garden.blockedCount} blocked`]
  if (garden.lanes.length) parts.push(`${garden.lanes.length} working`)
  if (landing) parts.push(`${landing} landing`)
  if (waiting) parts.push(`${waiting} queued`)
  return `${garden.approvals.length ? '🌿' : '🌸'} ${parts.join(' · ')}`
}
