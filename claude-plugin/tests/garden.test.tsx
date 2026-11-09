import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import { BAR_CELLS, failedToday, filledCells, needsYouRows, parseApprovals, parseRuns, parseTickets, readGarden } from '../hooks/garden-data'

const PANE = {
  plugin: 'loregarden',
  component: 'Pane',
  requestId: 'loregarden-garden',
  props: {
    title: 'Loregarden',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 80 },
    view: {},
  },
} as const

/** The person typing /garden in a fullscreen terminal. */
const GARDEN = {
  command: 'garden',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 120 },
} as const

const NOW = Date.parse('2026-11-09T16:00:00Z')

/** Shaped like the live server's answers: 14 identical gates on one ticket, as on 2026-11-09. */
const approvals = [
  ...Array.from({ length: 14 }, (_, i) => ({
    id: `b${i}`,
    kind: 'workflow_gate',
    title: 'Resolve Playtest exit actions',
    status: 'pending',
    workspace_slug: 'blobert',
    ticket_id: 'tb',
    ticket_external_id: 'blob-core-absorption-136',
  })),
  { id: 'l1', kind: 'human_action', title: 'Approve the gate change', status: 'pending', workspace_slug: 'loregarden', ticket_id: 't9', ticket_external_id: 'lg-plugin-panel-2530' },
  { id: 'old', kind: 'human_action', title: 'Already resolved', status: 'approved', ticket_id: 't9' },
]
const stage = (name: string, status: string) => ({ key: name.toLowerCase(), name, status })
const tickets = [
  {
    id: 't9',
    external_id: 'lg-plugin-panel-2530',
    title: 'Queue cards in the garden',
    workspace_slug: 'loregarden',
    state: 'in_progress',
    blocked_on: null,
    stages: [stage('Plan', 'done'), stage('Spec', 'done'), stage('Implement', 'running'), stage('Verify', 'pending')],
  },
  { id: 'tp', external_id: 'lg-person-9', title: 'Pick a name for Baxter', workspace_slug: 'loregarden', state: 'blocked', blocked_on: 'person', stages: [] },
  { id: 'tt', external_id: 'lg-dep-1', title: 'Blocked on another ticket', workspace_slug: 'loregarden', state: 'blocked', blocked_on: 'tickets', stages: [] },
]
const runs = [
  { id: 'r3', ticket_id: 't9', ticket_title: 'Queue cards in the garden', status: 'failed', agent_id: 'implementer', stage_key: 'implement', finished_at: '2026-11-09T15:30:00+00:00' },
  { id: 'r2', ticket_id: 't9', ticket_title: 'Queue cards in the garden', status: 'failed', agent_id: 'implementer', stage_key: 'implement', finished_at: '2026-11-09T15:10:00+00:00' },
  // Failed, but two days ago in every timezone: not "failed today".
  { id: 'r0', ticket_id: 'tt', ticket_title: 'Blocked on another ticket', status: 'failed', agent_id: 'implementer', stage_key: 'verify', finished_at: '2026-11-07T12:00:00+00:00' },
  { id: 'r1', ticket_id: 'tt', ticket_title: 'Blocked on another ticket', status: 'succeeded', agent_id: 'reference_surveyor', stage_key: 'survey', finished_at: '2026-11-08T12:00:00+00:00' },
]
const ships = { probes: [{ workspace_slug: 'loregarden', violations: [] }] }
const queue = {
  queues: [
    {
      workspace_slug: 'loregarden',
      entries: [
        { id: 'q1', ticket_id: 't9', pr_number: 830, pr_url: 'https://github.com/laazer/loregarden/pull/830', state: 'testing', position: 1, wait: '4m', ticket_title: 'Ship the plugin', state_changed_at: '2026-11-09T15:58:00' },
        { id: 'q0', pr_number: 829, pr_url: 'https://github.com/laazer/loregarden/pull/829', state: 'merged', position: 0, wait: '', source_branch: 'claude/spec-hash-on-runs', state_changed_at: '2026-11-09T12:00:00' },
      ],
    },
  ],
}
const capacity = {
  host: { ceiling: { cpus: 7, memory_mb: 48332 }, in_use: { cpus: 6, memory_mb: 12288 } },
  holders: [
    // The live case: a suite that reported FAILED 75 minutes ago and still holds 4 CPUs.
    {
      lease_id: 'h1',
      holder_label: '2490 full server suite (2) · paper-review-2610@claude/dispatch-idempotency · pid 79180',
      holder: { what: '2490 full server suite (2)', worktree: 'paper-review-2610', branch: 'claude/dispatch-idempotency', pid: 79180 },
      footprint: 'heavy',
      cpus: 4,
      memory_mb: 8192,
      held_seconds: 7200,
      progress: { summary: 'FAILED: pytest on 1 file(s) (5/5)', reported_at: '2026-11-09T14:45:00+00:00' },
    },
    // Held by the run on lane 1: drawn on that ticket's card, not as its own.
    { lease_id: 'h2', holder_label: 'run r9', agent_run_id: 'r9', ticket_id: 't9', footprint: 'stack', cpus: 2, memory_mb: 4096, held_seconds: 300 },
  ],
  waiting: [{ lease_id: 'w1', holder_label: 'jest · claude/y', footprint: 'stack', position: 1, estimated_wait_seconds: 600 }],
}
const lanes = {
  active_runs: [
    { run_id: 'r9', orchestration_run_id: 'o1', ticket_id: 't9', slot_number: 1, agent_id: 'implementer', stage_key: 'implement', status: 'running', elapsed_seconds: 1500 },
  ],
}
// Waiting for a lane, on a ticket the ticket list does not hold: named by a detail read.
const laneQueue = {
  queued_runs: [
    { entry_id: 'e1', ticket_id: 't8', slot_number: 1, position: 1, entry_kind: 'orchestration', agent_id: 'planner', stage_key: 'plan', wait_seconds: 240 },
  ],
}
const t8 = { id: 't8', external_id: 'blo-77', title: 'Playtest exit actions', workspace_slug: 'blobert' }

const ANSWERS: Record<string, unknown> = {
  '/api/inbox/approvals': approvals,
  '/api/tickets/status-summary': { running: 1, in_progress: 68, blocked: 31 },
  '/api/done-ticket-ship': ships,
  '/api/merge-queue': queue,
  '/api/parallel/active-runs': lanes,
  '/api/parallel/queued-runs': laneQueue,
  '/api/parallel/lanes/count': { lane_count: 6, min: 1, max: 12 },
  '/api/tickets?state=in_progress&state=blocked&state=parked': tickets,
  '/api/runs?limit=100': runs,
  '/api/docker/capacity': capacity,
  '/api/tickets/t8': t8,
}

type Answer = { status: number; ok: boolean; headers: Record<string, string>; text: string }
const ok = (body: unknown): Answer => ({ status: 200, ok: true, headers: {}, text: JSON.stringify(body) })
const down: Answer = { status: 502, ok: false, headers: {}, text: '' }

/** The engine beneath the plugin: a server answering by path, and a record of the status line and toasts. */
function engine(on: On, answer: (path: string) => Answer) {
  const seen = { status: [] as (string | undefined)[], toasts: [] as string[] }
  on('command.register', () => ({ value: { command: 'garden' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('clock.now', () => ({ value: NOW }))
  // Timers never fire here: each test reads through /garden, so nothing outlives it.
  on('clock.every', () => ({ deny: 'no timers in a test' }))
  on('clock.after', () => ({ deny: 'no timers in a test' }))
  on('http.fetch', (_, e) => ({ value: answer(e.url.replace('http://127.0.0.1:8000', '')) }))
  on('ui.status', (_, e) => {
    seen.status.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', (_, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  return seen
}

const SERVER = (path: string) => (path in ANSWERS ? ok(ANSWERS[path]) : down)

test("home's sections, in home's order, answer from the server on every surface", async ($, on) => {
  const seen = engine(on, SERVER)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)

  // 15 approvals, 1 ticket waiting on a person.
  expect(seen.status.at(-1)).toBe('🌿 16 need you · 1 running · 1 landing · 1 waiting for capacity')

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })

    // Needs you: 14 identical gates are one row ×14; the failures are one row per ticket and stage.
    expect(await ui.find({ type: 'Text', text: /15 approvals · 2 failed · 1 ticket/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /^Resolve Playtest exit actions ×14$/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /^Queue cards in the garden ×2$/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /Pick a name for Baxter/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /Blocked on another ticket$/ })).toBeDefined() // the feed, not Needs you

    // Running now: the card has the stage strip, and the lease its run holds.
    expect(await ui.find({ type: 'Text', text: /1 of 6 lanes/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /2 of 4 stages/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /holds 2 CPU · 4\.0 GB/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /Playtest exit actions$/ })).toBeDefined()

    // Landing on main.
    expect(await ui.find({ type: 'Text', text: /1 in line/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /^#830$/ })).toBeDefined()

    // Machine capacity: the bars, the silent holder's card, and the line.
    expect(await ui.find({ type: 'Text', text: /CPU 6\/7/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /run r9/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /pid 79180/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /silent for 1h, still holding/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Waiting for capacity · 1/ })).toBeDefined()

    // Recent activity.
    expect(await ui.find({ type: 'Text', text: /reference_surveyor/ })).toBeDefined()
    await ui.unmount()
  }
})

test('an unreachable server says so, with a way to retry, and toasts once per outage', async ($, on) => {
  const seen = engine(on, () => down)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ type: 'Text', text: /out of reach/ })).toBeDefined()
  await ui.press({ key: 'retry' })

  expect(seen.toasts).toHaveLength(1)
  expect(seen.status.at(-1)).toBe('🥀 Loregarden unreachable')
  await ui.unmount()
})

test('one source failing names it and leaves the other sections working', async ($, on) => {
  engine(on, path => (path === '/api/inbox/approvals' ? down : SERVER(path)))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ type: 'Text', text: /did not answer: approvals \(HTTP 502\)/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /pid 79180/ })).toBeDefined()
  await ui.unmount()
})

test('a quiet garden is a set of empty states, not an error', async ($, on) => {
  const quiet: Record<string, unknown> = {
    '/api/inbox/approvals': [],
    '/api/tickets?state=in_progress&state=blocked&state=parked': [],
    '/api/runs?limit=100': [],
    '/api/merge-queue': { queues: [] },
    '/api/parallel/active-runs': { active_runs: [] },
    '/api/parallel/queued-runs': { queued_runs: [] },
    '/api/docker/capacity': { host: capacity.host, holders: [], waiting: [] },
  }
  engine(on, path => (path in quiet ? ok(quiet[path]) : SERVER(path)))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(await ui.find({ type: 'Text', text: /^Nothing needs you/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Nothing running; 6 lanes free/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Nothing is landing on main right now; 0 pull requests/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Nothing holds the machine/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^No runs yet/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /out of reach|did not answer/ })).toBeUndefined()
  await ui.unmount()
})

test('more than ten things to do show ten, and Show all lists the rest', async ($, on) => {
  const many = Array.from({ length: 12 }, (_, i) => ({ ...approvals[14], id: `m${i}`, title: `Distinct ask ${i}` }))
  engine(on, path => (path === '/api/inbox/approvals' ? ok(many) : SERVER(path)))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ type: 'Link', text: /Pick a name for Baxter/ })).toBeUndefined()
  await ui.press({ key: 'show-all' })
  expect(await ui.find({ type: 'Link', text: /Pick a name for Baxter/ })).toBeDefined()
  await ui.unmount()
})

test("Needs you groups as Home does, and leaves out tickets blocked on other tickets", () => {
  const rows = needsYouRows(parseApprovals(approvals), [], failedToday(parseRuns(runs), NOW).runs, parseTickets(tickets))
  expect(rows.map(r => [r.kind, r.title, r.count, r.where])).toEqual([
    ['approval', 'Resolve Playtest exit actions', 14, 'blob-core-absorption-136'],
    ['approval', 'Approve the gate change', 1, 'lg-plugin-panel-2530'],
    ['failed', 'Queue cards in the garden', 2, 'implement'],
    ['person', 'Pick a name for Baxter', 1, 'lg-person-9'],
  ])
})

test('readGarden throws only when every source fails', async () => {
  await expect(readGarden(async () => down, 'http://x', NOW, null, true)).rejects.toThrow(/did not answer/)
  const partial = await readGarden(
    async url => (url.endsWith('/merge-queue') ? down : SERVER(url.replace('http://x', ''))),
    'http://x/',
    NOW,
    null,
    true,
  )
  expect(partial.failed).toEqual(['queue (HTTP 502)'])
})

test('a light read keeps what the heavy read found, asks nothing heavy, and reuses ticket names', async () => {
  const asked: string[] = []
  const fetcher = async (url: string) => {
    asked.push(url.replace('http://x', ''))
    return SERVER(url.replace('http://x', ''))
  }
  const heavy = await readGarden(fetcher, 'http://x', NOW, null, true)
  expect(asked).toContain('/api/tickets/t8')
  asked.length = 0
  const light = await readGarden(fetcher, 'http://x', NOW + 1, heavy, false)
  expect(asked.sort()).toEqual([
    '/api/done-ticket-ship',
    '/api/inbox/approvals',
    '/api/merge-queue',
    '/api/parallel/active-runs',
    '/api/parallel/lanes/count',
    '/api/parallel/queued-runs',
    '/api/tickets/status-summary',
  ])
  expect(light.tickets).toEqual(heavy.tickets)
  expect(light.heavyAt).toBe(NOW)
  expect(light.names.t8?.title).toBe('Playtest exit actions')
})

test('a ticket name that cannot be read is named, and read again next poll', async () => {
  const names = (path: string) => (path === '/api/tickets/t8' ? down : SERVER(path))
  const first = await readGarden(async url => names(url.replace('http://x', '')), 'http://x', NOW, null, true)
  expect(first.failed).toEqual(['ticket names (HTTP 502)'])
  expect(first.names).toEqual({})
  const second = await readGarden(async url => SERVER(url.replace('http://x', '')), 'http://x', NOW + 1, first, false)
  expect(second.failed).toEqual([])
  expect(Object.keys(second.names)).toEqual(['t8'])
})

test('a meter fills in proportion and stays inside its cells, even past the ceiling', () => {
  expect(filledCells(4, 7)).toBe(6)
  expect(filledCells(0, 7)).toBe(0)
  expect(filledCells(9, 7)).toBe(BAR_CELLS)
  expect(filledCells(3, 0)).toBe(0)
})
