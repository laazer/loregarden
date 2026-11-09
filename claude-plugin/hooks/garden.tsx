import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Fetch, Garden, Lease } from '../types'
import {
  ago,
  BAR_CELLS,
  countLine,
  failedToday,
  filledCells,
  inLine,
  isActive,
  landedSince,
  leaseFor,
  needsYouRows,
  needYou,
  parseTime,
  QUIET_SECONDS,
  quietSeconds,
  readGarden,
  span,
  statusText,
  stopped,
  type Tone,
} from './garden-data'

const PANE = 'loregarden-garden'
const TITLE = 'Loregarden'
/** The status line's poll: light sources only, unless the panel is in use. */
const POLL_MS = 60_000
/** Opening or refreshing the panel re-reads the heavy sources once they are this old. */
const HEAVY_STALE_MS = 60_000
/** Home's limits: ten rows to do, five landing, ten in the feed. */
const NEEDS_ROWS = 10
const LANDING_ROWS = 5
const FEED_ROWS = 10
const DAY_MS = 24 * 60 * 60 * 1000

// Mirrors client/src/index.css: Claude Code cannot read the app's CSS tokens.
const SAGE = '#6fae8f'
const SAGE_LIGHT = '#8fc9ab'
const AMBER = '#e5a72c'
const RED = '#ff6a54'
const VIOLET = '#9d8cff'
const BLUE = '#4b9bff'
const MUTED = '#b3b1a3'

/** StatusChip's tones, as text colors. */
const TONE: Record<Tone | 'done' | 'idle', string> = {
  you: VIOLET,
  bad: RED,
  wait: AMBER,
  run: BLUE,
  done: MUTED,
  idle: MUTED,
}
const RUN_TONE: Record<string, Tone | 'done' | 'idle'> = {
  queued: 'wait',
  running: 'run',
  awaiting_permission: 'you',
  succeeded: 'done',
  failed: 'bad',
  cancelled: 'idle',
  blocked: 'bad',
  repairing: 'wait',
}
/**
 * Every mark the panel draws is this one square, colored by what it means:
 * a used bar cell, a free one, a capacity holder. Fonts give ▰ and ▱ different
 * shapes and sizes, and ☁ comes out as an emoji, so they never lined up.
 */
const MARK = '■'

/** One glyph per stage, so "5 of 12" reads at a glance (Home's stage strip). */
const STAGE_GLYPH: Record<string, [string, string]> = {
  done: ['●', SAGE],
  running: ['◉', BLUE],
  awaiting: ['◉', VIOLET],
  blocked: ['✕', RED],
}

// The tag changes when Garden's fields change: a value an earlier version stored reads as null.
const garden = atom({ plugin: 'loregarden', key: 'garden' } as const, null as Garden | null, {
  shape: 'garden-home-1',
})
const fetchState = atom({ plugin: 'loregarden', key: 'fetch' } as const, {
  inFlight: false,
  error: '',
} as Fetch)
const activeAt = atom({ plugin: 'loregarden', key: 'activeAt' } as const, 0)
const showAll = atom({ plugin: 'loregarden', key: 'showAll' } as const, false)

/**
 * One read of the server: the light sources always, the heavy ones when asked
 * or while the panel is in use. A read asked for while one runs is dropped.
 */
async function refresh($: EngineInterface, serverUrl: string, heavy = false): Promise<void> {
  const before = await read($, fetchState)
  if (before.inFlight) return
  await update($, fetchState, s => ({ ...s, inFlight: true }))
  try {
    const now = await $.clock.now()
    const withHeavy = heavy || isActive(await read($, activeAt), now)
    const fresh = await readGarden(url => $.http.fetch(url), serverUrl, now, await read($, garden), withHeavy)
    await update($, garden, () => fresh)
    await update($, fetchState, () => ({ inFlight: false, error: '' }))
    $.ui.status(statusText(fresh, ''))
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    await update($, fetchState, () => ({ inFlight: false, error: message }))
    $.ui.status(statusText(null, message))
    // Once per outage, not on every poll: the status line keeps saying it.
    if (!before.error) $.ui.toast(`🥀 ${message}`)
  }
}

/** "read just now", "read 3m ago". */
const readAgo = (at: number, now: number): string => {
  const said = ago(new Date(at).toISOString(), now)
  return said === 'now' ? 'read just now' : `read ${said} ago`
}

/** The person used the panel: keep heavy reads on, and re-read them if they are stale. */
async function use($: EngineInterface, serverUrl: string, force: boolean): Promise<void> {
  const now = await $.clock.now()
  await update($, activeAt, () => now)
  const g = await read($, garden)
  if (force || !g || now - g.heavyAt >= HEAVY_STALE_MS) await refresh($, serverUrl, true)
}

/** Home's greeting, from the local hour. */
function greeting(now: number): string {
  const hour = new Date(now).getHours()
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

/** "14:05" today, "Sat 14:05" before: Home's feed times. */
function timeLabel(iso: string, now: number): string {
  const at = parseTime(iso)
  if (at === null) return ''
  const when = new Date(at)
  const time = when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return when.toDateString() === new Date(now).toDateString()
    ? time
    : `${when.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`
}

export const register: Register = (on, options) => {
  const serverUrl = String(options.serverUrl ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
  // localhost, not 127.0.0.1: desktop makes links only of https: and http://localhost.
  const appUrl = String(options.appUrl ?? 'http://localhost:5173').replace(/\/+$/, '')

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'garden',
      description: 'Open Loregarden home beside the chat: what needs you, what is running, the machine',
    })
    $.clock.every(POLL_MS, () => void refresh($, serverUrl))
    // After the session has started, not during it: the first read takes about a second.
    $.clock.after(0, () => void refresh($, serverUrl))
    return next(e)
  })

  on('command.run', { command: 'garden' }, async $ => {
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    await use($, serverUrl, true)
    return {
      text: opened.isPlaced
        ? 'Loregarden panel opened.'
        : 'Loregarden panel opened; this surface shows panes only in its fullscreen layout.',
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const g = await read($, garden)
    const f = await read($, fetchState)
    const all = await read($, showAll)
    const now = await $.clock.now()
    const link = (path: string) => `${appUrl}${path}`

    const chip = (tone: Tone | 'done' | 'idle', label: string) => (
      <Text color={TONE[tone]} bold>
        [{label}]
      </Text>
    )
    /** Home's section head: name, count, and the link to the page with the rest. */
    const section = (title: string, count: string, to: { href: string; label: string }, body: RenderChildren) => (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between" gap={1}>
          <Text bold>
            {title} <Text dimColor>{count}</Text>
          </Text>
          <Link href={to.href} label={to.label} />
        </Box>
        {body}
      </Box>
    )
    /** Home's capacity meter: used cells in sage, free ones muted, the same square for both. */
    const meter = (label: string, used: number, ceiling: number, said: string) => {
      const filled = filledCells(used, ceiling)
      return (
        <Text tabular>
          <Text color={SAGE}>{MARK.repeat(filled)}</Text>
          <Text color={MUTED} dimColor>
            {MARK.repeat(BAR_CELLS - filled)}
          </Text>{' '}
          {label} {said}
        </Text>
      )
    }
    const empty = (text: string) => (
      <Text dimColor wrap="wrap">
        {text}
      </Text>
    )

    const header = (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between">
          <Text color={SAGE}>
            🌿 {new Date(now).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}
          </Text>
          <Box flexDirection="row" gap={1}>
            <Text dimColor>{f.inFlight ? 'watering…' : g ? readAgo(g.lightAt, now) : ''}</Text>
            <Button
              key="refresh"
              hotkey="r"
              label={f.inFlight ? 'Watering…' : '💧 Refresh'}
              onPress={() => use($, serverUrl, true)}
            />
          </Box>
        </Box>
        <Text bold color={SAGE_LIGHT}>
          {greeting(now)}
        </Text>
      </Box>
    )
    const frame = (body: RenderChildren) => (
      <Box flexDirection="column" gap={1}>
        {header}
        {body}
      </Box>
    )

    // Error with nothing to show: the server never answered.
    if (!g && f.error) {
      return frame(
        <Box flexDirection="column" gap={1} borderStyle="round" borderColor={RED} paddingX={1}>
          <Text color={RED} bold>
            🥀 The garden is out of reach
          </Text>
          <Text wrap="wrap">{f.error}</Text>
          <Text dimColor wrap="wrap">
            Start the server from the loregarden checkout with `task dev`, or point the plugin's serverUrl
            setting at the right address in /config.
          </Text>
          <Button key="retry" variant="primary" label="Try again" onPress={() => use($, serverUrl, true)} />
        </Box>,
      )
    }
    // Loading: nothing read yet.
    if (!g) return frame(<Text color={SAGE_LIGHT}>🌱 Sprouting… reading the garden.</Text>)

    /** A ticket's title, its id line, and its page; from the ticket list, else a name read for a card. */
    const ticket = (id: string) => {
      const t = g.tickets?.find(x => x.id === id)
      const n = g.names[id]
      const externalId = t?.externalId ?? n?.externalId ?? ''
      return {
        t,
        href: link(`/tickets/${externalId || id}`),
        title: t?.title ?? n?.title ?? `ticket ${id.slice(0, 8)}`,
        sub: [t?.workspace ?? n?.workspace, externalId].filter(Boolean).join(' · '),
      }
    }

    const failures = g.runs ? failedToday(g.runs, now) : null
    const rows = needsYouRows(g.approvals, g.ships, failures?.runs ?? [], g.tickets ?? [])
    const kpiList: [string, string, string | undefined][] = [
      ['need you', g.tickets ? String(needYou(g)) : '…', VIOLET],
      [
        'failed today',
        failures ? `${failures.runs.length}${failures.complete ? '' : '+'}` : '…',
        failures?.runs.length ? RED : undefined,
      ],
      ['running', g.counts ? String(g.counts.running) : '…', undefined],
      ['in progress', g.counts ? String(g.counts.inProgress) : '…', undefined],
    ]
    const kpis = (
      <Box flexDirection="row" gap={2} flexWrap="wrap">
        {kpiList.map(([label, value, color]) => (
          <Box key={`kpi-${label}`} flexDirection="column">
            <Text bold color={color} tabular>
              {value}
            </Text>
            <Text dimColor>{label}</Text>
          </Box>
        ))}
      </Box>
    )

    const banners = (
      <Box flexDirection="column">
        {f.error ? (
          <Text color={AMBER} wrap="wrap">
            🥀 The last read failed ({f.error}); showing what was {readAgo(g.lightAt, now)}.
          </Text>
        ) : null}
        {g.failed.length ? (
          <Text color={AMBER} wrap="wrap">
            🍂 Some sources did not answer: {g.failed.join(', ')}. What they feed below may be out of date.
          </Text>
        ) : null}
      </Box>
    )

    // ── Needs you ──
    const shownRows = all ? rows : rows.slice(0, NEEDS_ROWS)
    const ready = g.tickets !== null && g.runs !== null
    const needs = section(
      'Needs you',
      ready ? countLine(rows) : '…',
      { href: link('/'), label: 'Open inbox →' },
      <Box flexDirection="column">
        {ready && rows.length === 0
          ? empty(
              'Nothing needs you. Approvals, runs that failed today, tickets waiting on a person, and finished tickets whose ship is not green show up here.',
            )
          : null}
        {shownRows.map(r => (
          <Box key={r.key} flexDirection="row" gap={1}>
            {chip(r.chip.tone, r.chip.label)}
            <Link
              href={r.ticketId ? ticket(r.ticketId).href : link('/')}
              label={`${r.title}${r.count > 1 && r.kind !== 'person' ? ` ×${r.count}` : ''}`}
            />
            <Text dimColor wrap="truncate">
              {r.where}
            </Text>
          </Box>
        ))}
        {rows.length > NEEDS_ROWS ? (
          <Button
            key="show-all"
            variant="secondary"
            label={all ? 'Show fewer' : `Show all ${rows.length}`}
            onPress={() => update($, showAll, v => !v)}
          />
        ) : null}
        {ready ? null : <Text dimColor>Reading tickets and today's runs…</Text>}
      </Box>,
    )

    // ── Running now ──
    const holders = g.capacity?.holders ?? []
    /** What a held command last said, or how long it has been silent. */
    const saying = (l: Lease) => {
      const quiet = quietSeconds(l, now)
      if (quiet !== null && quiet >= QUIET_SECONDS) {
        return (
          <Text color={RED} wrap="wrap">
            ⚠ silent for {span(quiet)}, still holding · last: {l.progress?.summary}
          </Text>
        )
      }
      return l.progress ? (
        <Text dimColor wrap="truncate">
          {l.progress.summary} · {ago(l.progress.reportedAt, now)}
        </Text>
      ) : null
    }
    const holds = (l: Lease) => `${l.cpus} CPU · ${(l.memoryMb / 1024).toFixed(1)} GB`
    const total = g.laneTotal
    const running = section(
      'Running now',
      total === null ? '…' : `${Math.min(g.lanes.length, total)} of ${total} lanes`,
      { href: link('/machine/lanes'), label: 'Lanes →' },
      <Box flexDirection="column" gap={1}>
        {g.lanes.length === 0
          ? empty(`Nothing running${total === null ? '' : `; ${total} lanes free`}. Start a ticket from the Queue.`)
          : null}
        {g.lanes.map(lane => {
          const k = ticket(lane.ticketId)
          const stages = k.t?.stages ?? []
          const current = stages.find(s => s.status === 'running')
          const done = stages.filter(s => s.status === 'done').length
          const lease = leaseFor(lane, holders)
          const tone = RUN_TONE[lane.status] ?? 'run'
          return (
            <Box
              key={`lane-${lane.slot}-${lane.ticketId}`}
              flexDirection="column"
              borderStyle="round"
              borderColor={tone === 'wait' ? AMBER : SAGE}
              paddingX={1}
            >
              <Box flexDirection="row" justifyContent="space-between" gap={1}>
                <Text dimColor>{k.sub || `lane ${lane.slot}`}</Text>
                {chip(tone, current?.name || lane.stage || lane.status || 'running')}
              </Box>
              <Link href={k.href} label={k.title} />
              {stages.length ? (
                <Text>
                  {stages.map((s, i) => {
                    const [glyph, color] = STAGE_GLYPH[s.status] ?? ['○', MUTED]
                    return (
                      <Text key={`stage-${i}`} color={color}>
                        {glyph}
                      </Text>
                    )
                  })}
                </Text>
              ) : null}
              <Text dimColor>
                {stages.length ? `${done} of ${stages.length} stages · ` : ''}
                {span(lane.elapsedSeconds)}
                {lane.agent ? ` · ${lane.agent}` : ''} · lane {lane.slot}
              </Text>
              {lease ? (
                <Text color={AMBER}>
                  {MARK} holds {holds(lease)} for {span(lease.heldSeconds)}
                </Text>
              ) : null}
              {lease ? saying(lease) : null}
            </Box>
          )
        })}
        {g.laneQueue.length ? (
          <Box flexDirection="column">
            <Text dimColor>Waiting for a lane · {g.laneQueue.length}</Text>
            {g.laneQueue.map(q => {
              const k = ticket(q.ticketId)
              return (
                <Box flexDirection="row" gap={1} key={`lanewait-${q.id}`}>
                  <Text color={VIOLET} tabular>
                    #{q.position}
                  </Text>
                  <Link href={k.href} label={k.title} />
                  <Text dimColor>{[q.stage, span(q.waitSeconds)].filter(Boolean).join(' · ')}</Text>
                </Box>
              )
            })}
          </Box>
        ) : null}
      </Box>,
    )

    // ── Landing on main ──
    const waitingToLand = inLine(g.queue).sort((a, b) => a.position - b.position)
    const landingRows = [...waitingToLand, ...stopped(g.queue)]
    const landed = landedSince(g.queue, now - DAY_MS).length
    const landing = section(
      'Landing on main',
      `${waitingToLand.length} in line`,
      { href: link('/machine/lanes#merge-queue'), label: 'Merge queue →' },
      <Box flexDirection="column">
        {landingRows.length === 0
          ? empty(
              `Nothing is landing on main right now; ${landed === 1 ? '1 pull request' : `${landed} pull requests`} landed through the queue in the last 24 hours.`,
            )
          : null}
        {landingRows.slice(0, LANDING_ROWS).map(e => (
          <Box flexDirection="row" gap={1} key={`landing-${e.id}`}>
            {chip(e.state === 'failed' || e.state === 'timed_out' ? 'bad' : 'wait', e.state)}
            <Link href={e.url} label={`#${e.pr}`} />
            {e.ticketId ? (
              <Link href={ticket(e.ticketId).href} label={e.title} />
            ) : (
              <Text wrap="truncate">{e.title}</Text>
            )}
            {e.wait ? <Text dimColor>{e.wait}</Text> : null}
          </Box>
        ))}
        {landingRows.length > LANDING_ROWS ? (
          <Link
            href={link('/machine/lanes#merge-queue')}
            label={`${landingRows.length - LANDING_ROWS} more in the merge queue`}
          />
        ) : null}
      </Box>,
    )

    // ── Machine capacity ──
    const c = g.capacity
    const onLanes = new Set(g.lanes.map(l => leaseFor(l, holders)?.id).filter(Boolean))
    const loose = holders.filter(h => !onLanes.has(h.id))
    const machine = section(
      'Machine capacity',
      c ? `${c.holders.length} holding · ${c.waiting.length} waiting` : '…',
      { href: link('/machine'), label: 'Machine →' },
      !c ? (
        <Text dimColor>Reading the capacity ledger…</Text>
      ) : (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="column">
            {meter('CPU', c.cpusUsed, c.cpusCeiling, `${c.cpusUsed}/${c.cpusCeiling}`)}
            {meter(
              'Memory',
              c.memoryUsedMb,
              c.memoryCeilingMb,
              `${(c.memoryUsedMb / 1024).toFixed(1)}/${(c.memoryCeilingMb / 1024).toFixed(1)} GB`,
            )}
          </Box>
          {c.holders.length === 0 ? empty('☀️ Nothing holds the machine.') : null}
          {onLanes.size ? <Text dimColor>{onLanes.size} held by running tickets, on their cards above.</Text> : null}
          {loose.map(l => {
            const quiet = quietSeconds(l, now)
            const stuck = quiet !== null && quiet >= QUIET_SECONDS
            return (
              <Box
                key={`holder-${l.id}`}
                flexDirection="column"
                borderStyle="round"
                borderColor={stuck ? RED : AMBER}
                paddingX={1}
              >
                <Box flexDirection="row" justifyContent="space-between" gap={1}>
                  <Text bold wrap="truncate">
                    <Text color={stuck ? RED : AMBER}>{MARK}</Text> {l.what}
                  </Text>
                  <Text dimColor tabular>
                    {span(l.heldSeconds)}
                  </Text>
                </Box>
                {l.where ? (
                  <Text dimColor wrap="truncate">
                    {l.where}
                    {l.pid === null ? '' : ` · pid ${l.pid}`}
                  </Text>
                ) : null}
                <Text color={AMBER}>
                  {holds(l)}
                  <Text dimColor>{l.footprint ? ` · ${l.footprint}` : ''}</Text>
                </Text>
                {l.ticketId ? <Link href={ticket(l.ticketId).href} label={ticket(l.ticketId).title} /> : null}
                {saying(l)}
              </Box>
            )
          })}
          {c.waiting.length ? (
            <Box flexDirection="column">
              <Text dimColor>Waiting for capacity · {c.waiting.length}</Text>
              {c.waiting.map(w => (
                <Box flexDirection="row" gap={1} key={`waiting-${w.id}`}>
                  <Text color={VIOLET} tabular>
                    {w.position === null ? '·' : `#${w.position}`}
                  </Text>
                  <Text wrap="truncate">{w.what}</Text>
                  <Text color={w.pollStalled ? RED : undefined} dimColor={!w.pollStalled}>
                    {w.footprint}
                    {w.waitSeconds === null ? '' : ` · ~${span(w.waitSeconds)}`}
                    {w.pollStalled ? ' · stopped asking' : ''}
                  </Text>
                </Box>
              ))}
            </Box>
          ) : null}
        </Box>
      ),
    )

    // ── Recent activity ──
    const activity = section(
      'Recent activity',
      '',
      { href: link('/console'), label: 'Console →' },
      !g.runs ? (
        <Text dimColor>Reading recent runs…</Text>
      ) : g.runs.length === 0 ? (
        empty("No runs yet. A ticket's agent runs show up here as they finish.")
      ) : (
        <Box flexDirection="column">
          {g.runs.slice(0, FEED_ROWS).map(r => {
            const subject = r.ticketTitle || r.ticketExternalId || 'the workspace'
            return (
              <Box flexDirection="row" gap={1} key={`run-${r.id}`}>
                <Text dimColor tabular>
                  {timeLabel(r.at, now)}
                </Text>
                {chip(RUN_TONE[r.status] ?? 'idle', r.status)}
                <Text bold>{r.agent || 'agent'}</Text>
                {r.stage ? <Text dimColor>· {r.stage}</Text> : null}
                <Text>on</Text>
                {r.ticketId ? <Link href={ticket(r.ticketId).href} label={subject} /> : <Text wrap="truncate">{subject}</Text>}
              </Box>
            )
          })}
        </Box>
      ),
    )

    return frame(
      <Box flexDirection="column" gap={1}>
        {kpis}
        {banners}
        {machine}
        {needs}
        {running}
        {landing}
        {activity}
      </Box>,
    )
  })
}
