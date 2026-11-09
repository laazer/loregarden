import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Fetch, Garden, GardenTab } from '../types'
import {
  ago,
  bar,
  byWorkspace,
  isActive,
  isLanding,
  readGarden,
  span,
  statusText,
} from './garden-data'

const PANE = 'loregarden-garden'
const TITLE = 'Loregarden'
/** The status line's poll: light sources only, unless the panel is in use. */
const POLL_MS = 60_000
/** A tab switch re-reads the heavy sources once they are this old. */
const HEAVY_STALE_MS = 60_000
/** Rows shown per workspace group before a "more" link; real data has 14 approvals in one. */
const PER_GROUP = 5

// Mirrors client/src/index.css: Claude Code cannot read the app's CSS tokens.
const SAGE = '#6fae8f'
const SAGE_LIGHT = '#8fc9ab'
const AMBER = '#e5a72c'
const RED = '#ff6a54'
const VIOLET = '#9d8cff'

const tab = atom({ plugin: 'loregarden', key: 'tab' } as const, 'tend' as GardenTab)
// The tag changes when Garden's fields change: a value an earlier version stored reads as null.
const garden = atom({ plugin: 'loregarden', key: 'garden' } as const, null as Garden | null, {
  shape: 'garden-2',
})
const fetchState = atom({ plugin: 'loregarden', key: 'fetch' } as const, {
  inFlight: false,
  error: '',
} as Fetch)
const activeAt = atom({ plugin: 'loregarden', key: 'activeAt' } as const, 0)

const TABS: { id: GardenTab; hotkey: string; glyph: string; name: string; says: string }[] = [
  { id: 'tend', hotkey: '1', glyph: '🌱', name: 'Tend', says: 'Approvals waiting on you, and blocked tickets' },
  { id: 'harvest', hotkey: '2', glyph: '🌾', name: 'Harvest', says: 'Pull requests in the merge queue' },
  { id: 'weather', hotkey: '3', glyph: '⛅', name: 'Weather', says: 'Who holds this machine, and who waits' },
]

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

/** Shows a tab, reading what it needs if that is stale. */
async function show($: EngineInterface, serverUrl: string, id: GardenTab): Promise<void> {
  await update($, tab, () => id)
  await use($, serverUrl, false)
}

export const register: Register = (on, options) => {
  const serverUrl = String(options.serverUrl ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
  // localhost, not 127.0.0.1: desktop makes links only of https: and http://localhost.
  const appUrl = String(options.appUrl ?? 'http://localhost:5173').replace(/\/+$/, '')

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'garden',
      description: 'Open the Loregarden panel: what needs you, what is landing, how busy the machine is',
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
    const current = await read($, tab)
    const g = await read($, garden)
    const f = await read($, fetchState)
    const now = await $.clock.now()

    const link = (path: string) => `${appUrl}${path}`
    const counts: Record<GardenTab, string> = {
      tend: g ? String(g.approvals.length + g.blockedCount) : '…',
      harvest: g ? String(g.queue.filter(isLanding).length) : '…',
      weather: g?.capacity ? String(g.capacity.holders.length + g.capacity.waiting.length) : '…',
    }
    const shown = TABS.find(t => t.id === current) ?? TABS[0]!

    const header = (
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color={SAGE}>
          🌿 Loregarden
        </Text>
        <Text dimColor>
          {f.inFlight ? 'watering…' : g ? readAgo(g.lightAt, now) : ''}
        </Text>
      </Box>
    )

    const tabs = (
      <Box flexDirection="row" gap={1} flexWrap="wrap">
        {TABS.map(t => (
          <Button
            key={`tab-${t.id}`}
            hotkey={t.hotkey}
            variant={t.id === current ? 'primary' : 'secondary'}
            label={`${t.glyph} ${t.name} ${counts[t.id]}`}
            onPress={() => show($, serverUrl, t.id)}
          />
        ))}
        <Button
          key="refresh"
          hotkey="r"
          label={f.inFlight ? 'Watering…' : '💧 Refresh'}
          onPress={() => use($, serverUrl, true)}
        />
      </Box>
    )

    const frame = (body: RenderChildren) => (
      <Box flexDirection="column" gap={1}>
        {header}
        {tabs}
        <Text dimColor>{shown.says}</Text>
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

    const banners = (
      <Box flexDirection="column">
        {f.error ? (
          <Text color={AMBER} wrap="wrap">
            🥀 The last read failed ({f.error}); showing what was {readAgo(g.lightAt, now)}.
          </Text>
        ) : null}
        {g.failed.length ? (
          <Text color={AMBER} wrap="wrap">
            🍂 Some beds did not answer: {g.failed.join(', ')}. Their lists below are empty, not clear.
          </Text>
        ) : null}
      </Box>
    )

    const group = <T extends { workspace: string }>(
      title: string,
      rows: T[],
      empty: string,
      more: string,
      row: (r: T) => RenderChildren,
    ) => (
      <Box flexDirection="column">
        <Text bold>
          {title} <Text dimColor>{rows.length}</Text>
        </Text>
        {rows.length === 0 ? <Text dimColor>{empty}</Text> : null}
        {byWorkspace(rows).map(([ws, items]) => (
          <Box flexDirection="column" key={`${title}-${ws}`} marginTop={1}>
            <Text color={SAGE}>
              {ws} · {items.length}
            </Text>
            {items.slice(0, PER_GROUP).map(row)}
            {items.length > PER_GROUP ? (
              <Link href={link(more)} label={`+${items.length - PER_GROUP} more in Loregarden`} />
            ) : null}
          </Box>
        ))}
      </Box>
    )

    let body: RenderChildren
    if (current === 'tend') {
      const nothing = g.approvals.length === 0 && g.blockedCount === 0
      body = nothing ? (
        <Text color={SAGE_LIGHT}>🌸 Nothing needs you. The garden is tending itself.</Text>
      ) : (
        <Box flexDirection="column" gap={1}>
          {group('Approvals waiting on you', g.approvals, 'None waiting.', '/', a => (
            <Box flexDirection="row" gap={1} key={`approval-${a.id}`}>
              <Text color={a.level === 'high' ? RED : AMBER}>{a.level === 'high' ? '●' : '○'}</Text>
              <Link href={link(a.ticket ? `/tickets/${a.ticket}` : '/')} label={a.title} />
              <Text dimColor>
                {a.stage ? `${a.stage} · ` : ''}
                {ago(a.createdAt, now)}
              </Text>
            </Box>
          ))}
          {g.blocked === null ? (
            <Text dimColor>Blocked tickets {g.blockedCount}: reading the list…</Text>
          ) : null}
          {g.blocked !== null && group('Blocked tickets', g.blocked, 'Nothing blocked.', '/work-items', t => (
            <Box flexDirection="row" gap={1} key={`blocked-${t.id}`}>
              <Text color={VIOLET}>⊘</Text>
              <Link href={link(`/tickets/${t.externalId || t.id}`)} label={t.title} />
              <Text dimColor>{[t.type, t.kind].filter(Boolean).join(' · ')}</Text>
            </Box>
          ))}
        </Box>
      )
    } else if (current === 'harvest') {
      const landing = g.queue.filter(isLanding).sort((a, b) => a.position - b.position)
      const landed = g.queue
        .filter(e => e.state === 'merged')
        .sort((a, b) => b.changedAt.localeCompare(a.changedAt))
        .slice(0, 5)
      body = (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="column">
            <Text bold>
              Landing <Text dimColor>{landing.length}</Text>
            </Text>
            {landing.length === 0 ? (
              <Text dimColor>🌾 Nothing in the queue. Enqueue a PR with loregarden_enqueue_merge.</Text>
            ) : null}
            {landing.map(e => (
              <Box flexDirection="row" gap={1} key={`landing-${e.id}`}>
                <Text color={SAGE} tabular>
                  #{e.position}
                </Text>
                <Link href={e.url} label={`PR ${e.pr}`} />
                <Text wrap="truncate">{e.title}</Text>
                <Text color={AMBER}>{e.state}</Text>
                {e.wait ? <Text dimColor>{e.wait}</Text> : null}
              </Box>
            ))}
          </Box>
          {landed.length ? (
            <Box flexDirection="column">
              <Text bold dimColor>
                Recently landed
              </Text>
              {landed.map(e => (
                <Box flexDirection="row" gap={1} key={`landed-${e.id}`}>
                  <Text color={SAGE}>✓</Text>
                  <Link href={e.url} label={`PR ${e.pr}`} />
                  <Text dimColor wrap="truncate">
                    {e.title} · {ago(e.changedAt, now)} ago
                  </Text>
                </Box>
              ))}
            </Box>
          ) : null}
        </Box>
      )
    } else {
      const c = g.capacity
      body = !c ? (
        <Text color={AMBER}>🍂 The capacity ledger did not answer.</Text>
      ) : (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="column">
            <Text tabular>
              <Text color={SAGE}>{bar(c.cpusUsed, c.cpusCeiling)}</Text> CPU {c.cpusUsed}/{c.cpusCeiling}
            </Text>
            <Text tabular>
              <Text color={SAGE}>{bar(c.memoryUsedMb, c.memoryCeilingMb)}</Text> Memory{' '}
              {(c.memoryUsedMb / 1024).toFixed(1)}/{(c.memoryCeilingMb / 1024).toFixed(1)} GB
            </Text>
          </Box>
          <Box flexDirection="column">
            <Text bold>
              Holding <Text dimColor>{c.holders.length}</Text>
            </Text>
            {c.holders.length === 0 ? <Text dimColor>☀️ Clear skies: nothing holds the machine.</Text> : null}
            {c.holders.map(holder => (
              <Box flexDirection="row" gap={1} key={`holder-${holder.id}`}>
                <Text color={AMBER}>☁</Text>
                <Text wrap="truncate">{holder.label}</Text>
                <Text dimColor>
                  {holder.footprint} · {span(holder.heldSeconds)}
                </Text>
              </Box>
            ))}
          </Box>
          <Box flexDirection="column">
            <Text bold>
              Waiting <Text dimColor>{c.waiting.length}</Text>
            </Text>
            {c.waiting.length === 0 ? <Text dimColor>Nobody is waiting in line.</Text> : null}
            {c.waiting.map(w => (
              <Box flexDirection="row" gap={1} key={`waiting-${w.id}`}>
                <Text color={VIOLET} tabular>
                  {w.position === null ? '·' : `#${w.position}`}
                </Text>
                <Text wrap="truncate">{w.label}</Text>
                <Text dimColor>
                  {w.footprint}
                  {w.waitSeconds === null ? '' : ` · ~${span(w.waitSeconds)}`}
                </Text>
              </Box>
            ))}
          </Box>
        </Box>
      )
    }

    return frame(
      <Box flexDirection="column" gap={1}>
        {banners}
        {body}
      </Box>,
    )
  })
}
