import type {
  Approval,
  Capacity,
  Counts,
  Garden,
  Lane,
  LaneWait,
  Lease,
  QueueEntry,
  Run,
  Ship,
  Ticket,
  TicketName,
} from '../types'

/** The server's JSON, read field by field: a missing field becomes a neutral value. */
type Json = Record<string, unknown>

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const numOrNull = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null
const list = (v: unknown): Json[] => (Array.isArray(v) ? (v as Json[]) : [])
const obj = (v: unknown): Json => (v && typeof v === 'object' ? (v as Json) : {})

/** What each approval kind asks of the operator, as a chip word; Home's words. */
const APPROVAL_WORDS: Record<string, string> = {
  workflow_gate: 'Gate',
  cli_permission: 'Permission',
  cli_question: 'Question',
  human_action: 'Action',
  rework_pause: 'Rework',
  stage_park: 'Parked',
  block_decision: 'Decision',
}

/** Pending approvals only, as Home reads them. */
export function parseApprovals(body: unknown): Approval[] {
  return list(body)
    .filter(a => !str(a.status) || str(a.status) === 'pending')
    .map(a => ({
      id: str(a.id),
      label: str(a.title) || str(a.tool_name) || APPROVAL_WORDS[str(a.kind)] || str(a.kind) || 'Approval',
      kind: str(a.kind),
      workspace: str(a.workspace_slug),
      ticketId: str(a.ticket_id),
      ticketExternalId: str(a.ticket_external_id),
    }))
}

export function parseCounts(body: unknown): Counts {
  const c = obj(body)
  return { running: num(c.running), inProgress: num(c.in_progress), blocked: num(c.blocked) }
}

export function parseTickets(body: unknown): Ticket[] {
  const rows = Array.isArray(body) ? body : obj(body).items
  return list(rows).map(t => ({
    id: str(t.id),
    externalId: str(t.external_id),
    title: str(t.title) || 'Untitled ticket',
    workspace: str(t.workspace_slug),
    state: str(t.state),
    blockedOn: str(t.blocked_on),
    stages: list(t.stages).map(s => ({ name: str(s.name) || str(s.key), status: str(s.status) })),
  }))
}

export function parseRuns(body: unknown): Run[] {
  return list(body).map(r => ({
    id: str(r.id),
    ticketId: str(r.ticket_id),
    ticketTitle: str(r.ticket_title),
    ticketExternalId: str(r.ticket_external_id),
    status: str(r.status),
    agent: str(r.agent_id),
    stage: str(r.stage_key),
    at: str(r.finished_at) || str(r.started_at) || str(r.created_at),
  }))
}

export function parseShips(body: unknown): Ship[] {
  return list(obj(body).probes).flatMap(p =>
    list(p.violations).map(v => ({
      ticketId: str(v.ticket_id),
      externalId: str(v.external_id),
      title: str(v.title) || 'Untitled ticket',
      reason: str(v.reason),
      pr: numOrNull(v.pr_number),
      checks: Array.isArray(v.check_names) ? v.check_names.map(str).filter(Boolean) : [],
      branch: str(v.carrying_branch),
    })),
  )
}

export function parseQueue(body: unknown): QueueEntry[] {
  return list(obj(body).queues).flatMap(q =>
    list(q.entries).map(e => ({
      id: str(e.id),
      ticketId: str(e.ticket_id),
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

/** The host pool: what every repo on this machine queues against. */
export function parseCapacity(body: unknown): Capacity {
  const host = obj(obj(body).host)
  return {
    cpusUsed: num(obj(host.in_use).cpus),
    cpusCeiling: num(obj(host.ceiling).cpus),
    memoryUsedMb: num(obj(host.in_use).memory_mb),
    memoryCeilingMb: num(obj(host.ceiling).memory_mb),
    holders: list(obj(body).holders).map(parseLease),
    waiting: list(obj(body).waiting).map(parseLease),
  }
}

type Fetcher = (url: string) => Promise<{ ok: boolean; status: number; text: string }>

/** Read on every poll: about a second of server time together on the live database. */
export const LIGHT = {
  approvals: '/api/inbox/approvals',
  counts: '/api/tickets/status-summary',
  ships: '/api/done-ticket-ship',
  queue: '/api/merge-queue',
  lanes: '/api/parallel/active-runs',
  laneQueue: '/api/parallel/queued-runs',
  laneTotal: '/api/parallel/lanes/count',
} as const

/** Read only while the panel is in use: about 3 s each on the live database. Home reads the same. */
export const HEAVY = {
  tickets: '/api/tickets?state=in_progress&state=blocked&state=parked',
  runs: '/api/runs?limit=100',
  capacity: '/api/docker/capacity',
} as const

/** Home reads the last 100 runs; "failed today" is a floor when all of them are from today. */
export const RUN_LIMIT = 100

type Source = keyof typeof LIGHT | keyof typeof HEAVY

async function readJson(fetcher: Fetcher, url: string): Promise<unknown> {
  const res = await fetcher(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return JSON.parse(res.text)
}

/**
 * Reads the light sources, and the heavy ones when `heavy`, all at once, on
 * top of what was read before. A source that fails is named in `failed` and
 * keeps its last value, so one broken endpoint does not blank the others.
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
  const garden: Garden = {
    lightAt: now,
    heavyAt: heavy ? now : (previous?.heavyAt ?? 0),
    approvals: pick('approvals', parseApprovals, previous?.approvals ?? []),
    counts: pick('counts', parseCounts, previous?.counts ?? null),
    ships: pick('ships', parseShips, previous?.ships ?? []),
    queue: pick('queue', parseQueue, previous?.queue ?? []),
    lanes: pick('lanes', parseLanes, previous?.lanes ?? []),
    laneQueue: pick('laneQueue', parseLaneQueue, previous?.laneQueue ?? []),
    laneTotal: pick('laneTotal', b => numOrNull(obj(b).lane_count), previous?.laneTotal ?? null),
    names: previous?.names ?? {},
    tickets: pick('tickets', parseTickets, previous?.tickets ?? null),
    runs: pick('runs', parseRuns, previous?.runs ?? null),
    capacity: pick('capacity', parseCapacity, previous?.capacity ?? null),
    failed,
  }
  if (failed.length === names.length) {
    throw new Error(`Loregarden server at ${root} did not answer: ${failed.join('; ')}`)
  }
  const listed = new Set((garden.tickets ?? []).map(t => t.id))
  const wanted = [
    ...garden.lanes.map(l => l.ticketId),
    ...garden.laneQueue.map(q => q.ticketId),
    ...(garden.capacity ? [...garden.capacity.holders, ...garden.capacity.waiting].map(l => l.ticketId) : []),
  ].filter(id => id && !listed.has(id))
  garden.names = await nameTickets(fetcher, root, wanted, garden.names, failed)
  return garden
}

/** Ticket names read per poll at most: a detail read is about 0.9 s on the live database. */
export const NAMES_PER_READ = 6

/**
 * Names for the tickets cards point at that the ticket list does not hold:
 * kept ones reused, new ones read (a few per poll), and ones no card points at
 * any more dropped. A failed read is named in `failed`; its card shows the
 * ticket id until a later poll names it.
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

// ── Home's derivations (client/src/lib/homeNeedsYou.ts, mergeQueueRows) ──

export type Tone = 'you' | 'bad' | 'wait' | 'run'

/** One thing to do: Home's "Needs you" row. */
export type NeedsRow = {
  kind: 'approval' | 'ship' | 'failed' | 'person'
  key: string
  title: string
  chip: { tone: Tone; label: string }
  count: number
  /** The one ticket the row opens, or null when it spans tickets. */
  ticketId: string | null
  /** The ticket's id, or "N tickets", or the failing stage. */
  where: string
}

/** Parked, or blocked with a person as the one who can unblock it. */
export const waitsOnPerson = (t: Ticket): boolean => t.state === 'parked' || t.blockedOn === 'person'

/** Today's failures, and whether that count is complete (Home's `failedToday`). */
export function failedToday(runs: Run[], now: number): { runs: Run[]; complete: boolean } {
  const day = new Date(now)
  const midnight = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime()
  const today = (r: Run) => {
    const at = parseTime(r.at)
    return at !== null && at >= midnight
  }
  const oldest = runs.at(-1)
  const complete = runs.length < RUN_LIMIT || oldest === undefined || !today(oldest)
  return { runs: runs.filter(r => r.status === 'failed' && today(r)), complete }
}

function shipRows(ships: Ship[]): NeedsRow[] {
  const groups = new Map<string, Ship[]>()
  for (const ship of ships) {
    if (ship.reason !== 'never_reached_remote' && ship.pr === null) continue
    const key = ship.reason === 'never_reached_remote' ? `nrr-${ship.branch}` : `pr-${ship.pr}`
    groups.set(key, [...(groups.get(key) ?? []), ship])
  }
  return [...groups.entries()].map(([key, members]) => {
    const pick = [...members].sort((a, b) => a.externalId.localeCompare(b.externalId))[0]!
    const grouped = members.length > 1
    const tone: Tone = pick.reason === 'failing' ? 'bad' : pick.reason === 'pending' ? 'wait' : 'you'
    const checks = pick.checks.length
      ? ` · ${pick.checks.slice(0, 2).join(', ')}${pick.checks.length > 2 ? ` and ${pick.checks.length - 2} more` : ''}`
      : ''
    const said =
      pick.reason === 'never_reached_remote'
        ? 'never reached remote'
        : `PR #${pick.pr} ${pick.reason === 'pending' ? 'pending' : 'failing'}`
    return {
      kind: 'ship' as const,
      key: `ship-${key}`,
      title: grouped ? said : pick.title,
      chip: { tone, label: 'Ship' },
      count: members.length,
      ticketId: pick.ticketId,
      where: grouped ? `${members.length} tickets` : `${said}${pick.reason === 'never_reached_remote' ? '' : checks}`,
    }
  })
}

/** Home's four kinds of row, in the order to take them: approvals, ships, failures, people. */
export function needsYouRows(approvals: Approval[], ships: Ship[], failures: Run[], tickets: Ticket[]): NeedsRow[] {
  const groups = new Map<string, Approval[]>()
  for (const a of approvals) groups.set(a.label, [...(groups.get(a.label) ?? []), a])
  const approvalRows: NeedsRow[] = [...groups.entries()]
    .map(([title, group]) => {
      const ids = new Set(group.map(a => a.ticketId))
      const single = ids.size === 1 ? group[0]! : null
      return {
        kind: 'approval' as const,
        key: `approval-${title}`,
        title,
        chip: { tone: 'you' as const, label: APPROVAL_WORDS[group[0]!.kind] ?? 'Approve' },
        count: group.length,
        ticketId: single && single.ticketId ? single.ticketId : null,
        where: single ? single.ticketExternalId : `${ids.size} tickets`,
      }
    })
    // The biggest pile first: it is the one a single review clears most of.
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title))

  const failedGroups = new Map<string, Run[]>()
  for (const r of failures) {
    if (!r.ticketId) continue
    const key = `${r.ticketId}:${r.stage}`
    failedGroups.set(key, [...(failedGroups.get(key) ?? []), r])
  }
  const failedRows: NeedsRow[] = [...failedGroups.entries()].map(([key, group]) => {
    const r = group[0]!
    return {
      kind: 'failed' as const,
      key: `failed-${key}`,
      title: r.ticketTitle || r.ticketExternalId || 'A run',
      chip: { tone: 'bad' as const, label: 'Failed' },
      count: group.length,
      ticketId: r.ticketId,
      where: r.stage || r.agent,
    }
  })

  const personRows: NeedsRow[] = tickets.filter(waitsOnPerson).map(t => ({
    kind: 'person' as const,
    key: `person-${t.id}`,
    title: t.title,
    chip: { tone: 'you' as const, label: t.state === 'parked' ? 'Parked' : 'Person' },
    count: 1,
    ticketId: t.id,
    where: t.externalId,
  }))
  return [...approvalRows, ...shipRows(ships), ...failedRows, ...personRows]
}

/** "14 approvals · 2 failed · 3 tickets": Home's count line. */
export function countLine(rows: NeedsRow[]): string {
  const sum = (kind: NeedsRow['kind']) => rows.reduce((n, r) => n + (r.kind === kind ? r.count : 0), 0)
  const approvals = sum('approval')
  const ships = sum('ship')
  const failed = sum('failed')
  const people = rows.filter(r => r.kind === 'person').length
  return [
    approvals ? `${approvals} approval${approvals === 1 ? '' : 's'}` : '',
    ships ? `${ships} ship${ships === 1 ? '' : 's'}` : '',
    failed ? `${failed} failed` : '',
    people ? `${people} ticket${people === 1 ? '' : 's'}` : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

/** Home's "need you" figure: approvals, people, and tickets whose ship is not green. */
export const needYou = (g: Garden): number =>
  g.approvals.length + (g.tickets ?? []).filter(waitsOnPerson).length + new Set(g.ships.map(s => s.ticketId)).size

const FINISHED = new Set(['merged', 'withdrawn', 'failed', 'timed_out'])
/** Entries still in line; then the ones that failed or timed out, which wait on someone. */
export const inLine = (q: QueueEntry[]): QueueEntry[] => q.filter(e => !FINISHED.has(e.state))
export const stopped = (q: QueueEntry[]): QueueEntry[] => q.filter(e => e.state === 'failed' || e.state === 'timed_out')
export const landedSince = (q: QueueEntry[], since: number): QueueEntry[] =>
  q.filter(e => {
    const at = parseTime(e.changedAt)
    return e.state === 'merged' && at !== null && at >= since
  })

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

/** "3d", "5h", "12m", "now": how long ago an ISO time was, or "" when it does not parse. */
export function ago(iso: string, now: number): string {
  const at = parseTime(iso)
  return at === null ? '' : span(Math.max(0, (now - at) / 1000))
}

/** An ISO time in ms, or null. The server writes naive UTC; a time with no zone is read as UTC, not local. */
export function parseTime(iso: string): number | null {
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

/** The bar's width in cells. */
export const BAR_CELLS = 10

/** How many of the bar's cells are used, 0 to BAR_CELLS. */
export function filledCells(used: number, ceiling: number): number {
  return ceiling > 0 ? Math.max(0, Math.min(BAR_CELLS, Math.round((used / ceiling) * BAR_CELLS))) : 0
}

/** The status-line text: one glance at what the garden needs. */
export function statusText(garden: Garden | null, error: string): string {
  if (error) return '🥀 Loregarden unreachable'
  if (!garden) return '🌱 Loregarden…'
  const you = needYou(garden)
  const parts = [`${you} need you`]
  if (garden.lanes.length) parts.push(`${garden.lanes.length} running`)
  const landing = inLine(garden.queue).length
  if (landing) parts.push(`${landing} landing`)
  const waiting = garden.capacity?.waiting.length ?? 0
  if (waiting) parts.push(`${waiting} waiting for capacity`)
  return `${you ? '🌿' : '🌸'} ${parts.join(' · ')}`
}
