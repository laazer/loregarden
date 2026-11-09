import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import { readGarden } from '../hooks/garden-data'

const PANE = {
  plugin: 'loregarden',
  component: 'Pane',
  requestId: 'loregarden-garden',
  props: {
    title: 'Loregarden',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
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

/** Shaped like the live server's answers, at the live volume of one 14-row group. */
const approvals = [
  ...Array.from({ length: 14 }, (_, i) => ({
    id: `b${i}`,
    title: `Resolve Playtest exit actions ${i}`,
    level: i === 0 ? 'high' : 'medium',
    workspace_slug: 'blobert',
    stage_name: 'Playtest',
    ticket_external_id: `blo-${i}`,
    created_at: '2026-11-08T16:00:00',
  })),
  {
    id: 'l1',
    title: 'Approve the gate change',
    level: 'high',
    workspace_slug: 'loregarden',
    stage_name: 'Review',
    ticket_external_id: 'lg-x-1',
    created_at: '2026-11-09T15:00:00',
  },
]
const blocked = [
  {
    id: 't1',
    external_id: 'lg-verdict-outcome-133',
    title: 'Verdict & outcome integrity',
    workspace_slug: 'loregarden',
    block_kind: 'dependency',
    work_item_type: 'milestone',
  },
]
const queue = {
  queues: [
    {
      workspace_slug: 'loregarden',
      entries: [
        { id: 'q1', pr_number: 830, pr_url: 'https://github.com/laazer/loregarden/pull/830', state: 'testing', position: 1, wait: '4m', ticket_title: 'Ship the plugin', state_changed_at: '2026-11-09T15:58:00' },
        { id: 'q0', pr_number: 829, pr_url: 'https://github.com/laazer/loregarden/pull/829', state: 'merged', position: 0, wait: '', ticket_title: '', source_branch: 'claude/spec-hash-on-runs', state_changed_at: '2026-11-09T12:00:00' },
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
    // Held by the run on lane 1: drawn on that lane's card, not as its own.
    { lease_id: 'h2', holder_label: 'run r1', agent_run_id: 'r1', ticket_id: 't9', footprint: 'stack', cpus: 2, memory_mb: 4096, held_seconds: 300 },
  ],
  waiting: [{ lease_id: 'w1', holder_label: 'jest · claude/y', footprint: 'stack', position: 1, estimated_wait_seconds: 600 }],
}
const lanes = {
  active_runs: [
    { run_id: 'r1', orchestration_run_id: 'o1', ticket_id: 't9', slot_number: 1, agent_id: 'implementer', stage_key: 'implement', status: 'running', elapsed_seconds: 1500 },
  ],
}
const laneQueue = {
  queued_runs: [
    { entry_id: 'e1', ticket_id: 't8', slot_number: 1, position: 1, entry_kind: 'orchestration', agent_id: 'planner', stage_key: 'plan', wait_seconds: 240 },
  ],
}
const t9 = { id: 't9', external_id: 'lg-plugin-panel-2530', title: 'Queue cards in the garden', workspace_slug: 'loregarden' }
const t8 = { id: 't8', external_id: 'blo-77', title: 'Playtest exit actions', workspace_slug: 'blobert' }

const ANSWERS: Record<string, unknown> = {
  '/api/inbox/approvals': approvals,
  '/api/tickets/status-summary': { blocked: 1, backlog: 2054 },
  '/api/tickets?state=blocked&limit=500': blocked,
  '/api/merge-queue': queue,
  '/api/docker/capacity': capacity,
  '/api/parallel/active-runs': lanes,
  '/api/parallel/queued-runs': laneQueue,
  '/api/tickets/t9': t9,
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

test('each tab answers its question from the server, on every surface', async ($, on) => {
  const seen = engine(on, SERVER)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)

  expect(seen.status.at(-1)).toBe('🌿 15 to tend · 1 blocked · 1 working · 1 landing · 1 queued')

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })

    // Tend: approvals grouped by workspace, the biggest first, capped with a link to the rest.
    expect((await ui.find({ type: 'Text', text: /^blobert · 14$/ }))?.text).toBe('blobert · 14')
    expect(await ui.find({ type: 'Link', text: /\+9 more in Loregarden/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', text: /Verdict & outcome integrity/ })).toBeDefined()

    await ui.press({ key: 'tab-harvest' })
    expect(await ui.find({ type: 'Link', text: /PR 830/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Recently landed/ })).toBeDefined()

    await ui.press({ key: 'tab-busy' })
    // A lane card names its ticket, links it, and carries the lease its run holds.
    expect(await ui.find({ type: 'Link', text: /Queue cards in the garden/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /holds 2 CPU · 4\.0 GB/ })).toBeDefined()
    // That lease is not drawn twice; the other holder gets its own card, flagged as silent.
    expect(await ui.find({ type: 'Text', text: /run r1/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /2490 full server suite/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /pid 79180/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /silent for 1h, still holding/ })).toBeDefined()
    // Both lines are under Waiting: the lane queue and the capacity queue.
    expect(await ui.find({ type: 'Link', text: /Playtest exit actions/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /☁ #1/ })).toBeDefined()

    await ui.press({ key: 'tab-tend' })
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

test('one source failing names it and leaves the other tabs working', async ($, on) => {
  engine(on, path => (path === '/api/inbox/approvals' ? down : SERVER(path)))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ type: 'Text', text: /did not answer: approvals \(HTTP 502\)/ })).toBeDefined()
  expect(await ui.find({ type: 'Link', text: /Verdict & outcome integrity/ })).toBeDefined()
  await ui.unmount()
})

test('nothing to tend is an empty state, not an error', async ($, on) => {
  engine(on, path =>
    path === '/api/tickets/status-summary'
      ? ok({ blocked: 0 })
      : path === '/api/inbox/approvals' || path.startsWith('/api/tickets')
        ? ok([])
        : SERVER(path),
  )
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(await ui.find({ type: 'Text', text: /Nothing needs you/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /out of reach|did not answer/ })).toBeUndefined()
  await ui.unmount()
})

test('readGarden throws only when every source fails', async () => {
  await expect(readGarden(async () => down, 'http://x', NOW, null, true)).rejects.toThrow(/did not answer/)
  const partial = await readGarden(
    async url => (url.endsWith('/merge-queue') ? down : ok([])),
    'http://x/',
    NOW,
    null,
    true,
  )
  expect(partial.failed).toEqual(['queue (HTTP 502)'])
})

test('a light read keeps what the heavy read found, and asks nothing heavy', async () => {
  const asked: string[] = []
  const fetcher = async (url: string) => {
    asked.push(url.replace('http://x', ''))
    return SERVER(url.replace('http://x', ''))
  }
  const heavy = await readGarden(fetcher, 'http://x', NOW, null, true)
  asked.length = 0
  const light = await readGarden(fetcher, 'http://x', NOW + 1, heavy, false)
  // Ticket names were read once, by the heavy read; the light read reuses them.
  expect(asked.sort()).toEqual([
    '/api/inbox/approvals',
    '/api/merge-queue',
    '/api/parallel/active-runs',
    '/api/parallel/queued-runs',
    '/api/tickets/status-summary',
  ])
  expect(light.tickets.t9?.title).toBe('Queue cards in the garden')
  expect(light.blocked).toEqual(heavy.blocked)
  expect(light.heavyAt).toBe(NOW)
})

test('a ticket name that cannot be read shows the ticket id and is named, then is read again', async () => {
  const names = (path: string) => (path.startsWith('/api/tickets/t') ? down : SERVER(path))
  const first = await readGarden(async url => names(url.replace('http://x', '')), 'http://x', NOW, null, true)
  expect(first.failed).toEqual(['ticket names (HTTP 502)'])
  expect(first.tickets).toEqual({})
  const second = await readGarden(async url => SERVER(url.replace('http://x', '')), 'http://x', NOW + 1, first, false)
  expect(second.failed).toEqual([])
  expect(Object.keys(second.tickets).sort()).toEqual(['t8', 't9'])
})

test('nothing at work is an empty state on Busy', async ($, on) => {
  engine(on, path =>
    path === '/api/parallel/active-runs'
      ? ok({ active_runs: [] })
      : path === '/api/parallel/queued-runs'
        ? ok({ queued_runs: [] })
        : path === '/api/docker/capacity'
          ? ok({ host: capacity.host, holders: [], waiting: [] })
          : SERVER(path),
  )
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run(GARDEN)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'tab-busy' })

  expect(await ui.find({ type: 'Text', text: /No lane is busy/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Nothing else holds the machine/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Nobody is waiting/ })).toBeDefined()
  await ui.unmount()
})
