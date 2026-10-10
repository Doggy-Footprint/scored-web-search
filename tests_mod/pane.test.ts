import { expect, test } from 'claude-code/testing'

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const SS = 'mcp__scored-web-search__score_sources'
const JS = 'mcp__scored-web-search__judge_support'
const PANE = {
  plugin: 'scored-web-search', component: 'Pane', requestId: 'search-view', surface: 'terminal',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Scored search', isFocused: false, bodyColumns: 70, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

const T1 = [
  'SCORE VERDICT T  SIGNALS URL',
  ' 98.0 PRIMARY  1  cit=10 https://p.org/a',
  ' 70.0 SUPPORT  3  https://s.org/use',
  ' 66.0 SUPPORT  3  y2025 https://s.org/skip',
  ' 60.0 SUPPORT  3  https://s.org/unj',
  ' 55.0 SUPPORT  3  https://s.org/pend',
  ' 14.0 DROP     6  https://d.org/x',
].join('\n')

function base(on: any, opts: { stdout?: string[] | string, exit?: number, openDeny?: boolean, unplaced?: boolean, keepClosed?: boolean, panes?: any[] } = {}) {
  const opened: string[] = []
  const openArgs: any[] = [], toasts: string[] = [], closed: string[] = []
  const panes: any[] = [...(opts.panes ?? [])]
  let i = 0
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => {
    const outs = Array.isArray(opts.stdout) ? opts.stdout : [opts.stdout ?? T1]
    const s = outs[Math.min(i++, outs.length - 1)]
    return { value: { exitCode: opts.exit ?? 0, stdout: s, stderr: '' } }
  })
  on('ui.open', ($: any, e: any) => { opened.push(e.id); openArgs.push(e); if (!opts.keepClosed) panes.push({ id: e.id, title: e.title, isPlaced: !opts.unplaced }); return opts.openDeny ? { deny: 'no' } : { value: { isPlaced: !opts.unplaced } } })
  on('ui.panes', () => ({ value: [...panes] }))
  on('ui.toast', ($: any, e: any) => { toasts.push(e.text); return { value: undefined } })
  on('ui.close', ($: any, e: any) => { closed.push(e.id); return { value: undefined } })
  on('http.fetch', () => ({ value: { ok: true, status: 200, headers: {}, text: '<p>body</p>' } }))
  on('model.complete', ($: any, e: any) => {
    const p = JSON.stringify(e.prompt) + JSON.stringify(e.promptBlocks ?? '')
    return { value: { isAnswered: true, text: p.includes('/skip') ? 'SKIP|seo filler' : p.includes('/unj') ? 'garbage' : 'USE|data', usage: USAGE } }
  })
  on('tool.call', () => ({ result: 'fetched' }))
  on('command.register', () => ({ value: undefined }))
  on('tool.register', () => ({ value: undefined }))
  on('agent.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/tmp/x' }))
  on('classic.SessionStart', () => ({}))
  on('agent.spawn', ($: any, e: any) => ({ model: 'haiku', agentId: 'ag-' + (e.description ?? 'p') }))
  on('turn.complete', () => ({ text: '' }))
  return Object.assign(opened, { openArgs, toasts, closed })
}


const SEARCHER = 'scored-web-search:searcher'
const spawn = ($: any, description: string, subagentType = SEARCHER) => $.agent.spawn({ subagentType, description, prompt: 'x' })
const done = ($: any, agentId: string) => $.turn.complete({ turnId: 't-' + agentId, agentId, answer: '[]', durationMs: 1, isAborted: false, usage: null } as any)
const recs = (n: number) => Array.from({ length: n }, (_, i) => ({ url: 'https://r.org/' + i }))
const txt = async (ui: any, text: string | RegExp) => ui.find({ type: 'Text', text })

// 1. auto-open
test('searcher spawn opens pane with closeOnEscape', async ($, on) => {
  const o = base(on)
  await spawn($, 'alpha')
  expect(o).toEqual(['search-view'])
  expect(o.openArgs[0].closeOnEscape).toBe(true)
})

test('searcher spawn does not reopen when panes() lists it', async ($, on) => {
  const o = base(on)
  await spawn($, 'a'); await spawn($, 'b')
  expect(o).toEqual(['search-view'])
})

test('searcher spawn reopens when pane was closed (not in panes())', async ($, on) => {
  const o = base(on, { keepClosed: true })
  await spawn($, 'a'); await spawn($, 'b')
  expect(o).toEqual(['search-view', 'search-view'])
})

test('already-open pane from before: no open', async ($, on) => {
  const o = base(on, { panes: [{ id: 'search-view', title: 'Scored search', isPlaced: true }] })
  await spawn($, 'a')
  expect(o).toEqual([])
})

test('isPlaced false shows toast mentioning /search-view', async ($, on) => {
  const o = base(on, { unplaced: true })
  await spawn($, 'a')
  expect(o.toasts.length).toBe(1)
  expect(o.toasts[0]).toContain('/search-view')
})

test('isPlaced true shows no toast', async ($, on) => {
  const o = base(on)
  await spawn($, 'a')
  expect(o.toasts).toEqual([])
})

test('other agent types do not open', async ($, on) => {
  const o = base(on)
  await spawn($, 'other', 'general-purpose')
  expect(o).toEqual([])
})

test('score_sources does not open pane', async ($, on) => {
  const o = base(on)
  await $.tool.call({ tool: SS, records: [] })
  expect(o).toEqual([])
})

test('ui.open failure on spawn still spawns', async ($, on) => {
  base(on, { openDeny: true })
  const r = await spawn($, 'a')
  expect((r as any).agentId).toBe('ag-a')
})

// 2. command
test('/search-view command opens with closeOnEscape and returns {}', async ($, on) => {
  const o = base(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const r = await $.command.run({ command: 'search-view', args: '' })
  expect(r).toEqual({})
  expect(o).toEqual(['search-view'])
  expect(o.openArgs[0].closeOnEscape).toBe(true)
})

// 3. close button
test('Close button (role dismiss) closes the pane', async ($, on) => {
  const o = base(on)
  const ui = await $.ui.mount(PANE)
  const b = await ui.find({ type: 'Button' })
  expect(b).toBeDefined()
  expect((b as any).text).toBe('Close')
  expect((b as any).props.role).toBe('dismiss')
  await ui.press({ key: (b as any).key })
  expect(o.closed).toEqual(['search-view'])
})

// 4. tree
test('root round header with label and record count; sources indented under it', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: recs(4), round: 'llm eval' })
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, '▾ Search: llm eval  4 URLs')).toBeDefined()
  expect(await txt(ui, '   ')).toBeDefined()
  expect(await txt(ui, 'Sources')).toBeUndefined()
  expect(await txt(ui, /Searches \(/)).toBeUndefined()
})

test('missing label defaults to Search <k>', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: recs(1) })
  await $.tool.call({ tool: SS, records: recs(2) })
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, '▾ Search: Search 1  1 URLs')).toBeDefined()
  expect(await txt(ui, '▾ Search: Search 2  2 URLs')).toBeDefined()
})

test('follow-up nested with 3-space indent per depth; unknown parent is root', async ($, on) => {
  base(on, { stdout: 'H' })
  await $.tool.call({ tool: SS, records: recs(3), round: 'A' })
  await $.tool.call({ tool: SS, records: recs(2), round: 'B', parentRound: 'A' })
  await $.tool.call({ tool: SS, records: recs(1), round: 'C', parentRound: 'B' })
  await $.tool.call({ tool: SS, records: recs(5), round: 'D', parentRound: 'nope' })
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, '▾ Search: A  3 URLs')).toBeDefined()
  expect(await txt(ui, '   ▾ Follow-up: B  2 URLs')).toBeDefined()
  expect(await txt(ui, '      ▾ Follow-up: C  1 URLs')).toBeDefined()
  expect(await txt(ui, '▾ Search: D  5 URLs')).toBeDefined()
})

test('source stays in first round; re-score updates score/verdict', async ($, on) => {
  base(on, { stdout: ['H\n 50.0 SUPPORT  3  https://x.org/a', 'H\n 95.0 PRIMARY  1  https://x.org/a/\n 40.0 SUPPORT  3  https://y.org/b'] })
  await $.tool.call({ tool: SS, records: recs(1), round: 'A' })
  await $.tool.call({ tool: SS, records: recs(2), round: 'B', parentRound: 'A' })
  const ui = await $.ui.mount(PANE)
  // round A sources indent 3, round B (depth 1) sources indent 6
  const a = await txt(ui, /x\.org\/a/)
  expect(a).toBeDefined()
  expect(await txt(ui, ' 95 PRIMARY')).toBeDefined()
  expect(await txt(ui, ' 50 SUPPORT')).toBeUndefined()
  expect(await txt(ui, '      ')).toBeDefined() // y.org under follow-up
  expect(await txt(ui, '2 collected → 2 passed → 0 judged-out → 0 read')).toBeDefined()
  const tree = JSON.stringify((ui as any).tree ?? (ui as any).root ?? '')
  if (tree.length > 2) {
    expect(tree.indexOf('x.org/a')).toBeLessThan(tree.indexOf('Follow-up: B'))
    expect(tree.indexOf('Follow-up: B')).toBeLessThan(tree.indexOf('y.org/b'))
  }
})

// 5,6. statuses
test('summary, statuses, judged-out lines, ✓ READ', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: JS, question: 'q', sources: [{ url: 'https://s.org/use' }, { url: 'https://s.org/skip' }, { url: 'https://s.org/unj' }] })
  await $.tool.call({ tool: 'WebFetch', url: 'https://p.org/a/#frag', prompt: 'x' })
  await $.tool.call({ tool: 'WebFetch', url: 'https://unscored.org/', prompt: 'x' })
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, '6 collected → 5 passed → 2 judged-out → 1 read')).toBeDefined()
  expect(await txt(ui, /✓ READ/)).toBeDefined()
  expect(await txt(ui, /^READ/)).toBeUndefined()
  for (const s of ['USE    ', 'SKIP   ', 'PENDING', 'OUT    ']) expect(await txt(ui, s)).toBeDefined()
  expect(await txt(ui, 'PASS   ')).toBeUndefined()
  expect(await txt(ui, /↳ SKIP: seo filler$/)).toBeDefined()
  expect(await txt(ui, /↳ UNJUDGED: /)).toBeDefined()
  expect(await txt(ui, /unscored/)).toBeUndefined()
})

test('WebFetch passes through', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: [] })
  const r = await $.tool.call({ tool: 'WebFetch', url: 'https://p.org/a', prompt: 'x' })
  expect(r.result).toBe('fetched')
  const r2 = await $.tool.call({ tool: 'WebFetch', url: 'https://unscored.org', prompt: 'x' })
  expect(r2.result).toBe('fetched')
})

test('PASS for unread PRIMARY; merge by URL', async ($, on) => {
  base(on, { stdout: [T1, 'SCORE VERDICT T  SIGNALS URL\n 99.0 DROP     6  https://s.org/pend/#x\n 50.0 PRIMARY  1  https://n.org/'] })
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: SS, records: [] })
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, 'PASS   ')).toBeDefined()
  expect(await txt(ui, '7 collected → 5 passed → 0 judged-out → 0 read')).toBeDefined()
})

// 7. pending searches
test('pending searcher shown until its turn.complete; others ignored', async ($, on) => {
  base(on)
  await spawn($, 'alpha'); await spawn($, 'gamma'); await spawn($, 'other', 'general-purpose')
  let ui = await $.ui.mount(PANE)
  expect(await txt(ui, '… searching: alpha')).toBeDefined()
  expect(await txt(ui, '… searching: gamma')).toBeDefined()
  expect(await txt(ui, /other/)).toBeUndefined()
  await ui.unmount()
  await done($, 'ag-alpha')
  ui = await $.ui.mount(PANE)
  expect(await txt(ui, /searching: alpha/)).toBeUndefined()
  expect(await txt(ui, '… searching: gamma')).toBeDefined()
})

// 8. reset
for (const source of ['clear', 'resume', 'fork']) {
  test(`SessionStart ${source} resets state`, async ($, on) => {
    base(on)
    await spawn($, 'alpha')
    await $.tool.call({ tool: SS, records: recs(2), round: 'R' })
    await $.classic.SessionStart({ source } as any)
    const ui = await $.ui.mount(PANE)
    expect(await txt(ui, '0 collected → 0 passed → 0 judged-out → 0 read')).toBeDefined()
    expect(await txt(ui, /▾/)).toBeUndefined()
    expect(await txt(ui, /searching/)).toBeUndefined()
  })
}

test('SessionStart startup does not reset', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: recs(2), round: 'R' })
  await $.classic.SessionStart({ source: 'startup' } as any)
  const ui = await $.ui.mount(PANE)
  expect(await txt(ui, '▾ Search: R  2 URLs')).toBeDefined()
})
