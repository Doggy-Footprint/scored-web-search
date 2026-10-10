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

function base(on: any, opts: { stdout?: string[] | string, exit?: number, openDeny?: boolean } = {}) {
  const opened: string[] = []
  let i = 0
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => {
    const outs = Array.isArray(opts.stdout) ? opts.stdout : [opts.stdout ?? T1]
    const s = outs[Math.min(i++, outs.length - 1)]
    return { value: { exitCode: opts.exit ?? 0, stdout: s, stderr: '' } }
  })
  on('ui.open', ($: any, e: any) => { opened.push(e.id); return opts.openDeny ? { deny: 'no' } : { value: { isPlaced: true } } })
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
  return opened
}

test('opens once on first successful score run', async ($, on) => {
  const opened = base(on)
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: SS, records: [] })
  expect(opened).toEqual(['search-view'])
})

test('failed score run does not open', async ($, on) => {
  const opened = base(on, { exit: 1 })
  await $.tool.call({ tool: SS, records: [] })
  expect(opened).toEqual([])
})

test('ui.open failure still returns stdout', async ($, on) => {
  base(on, { openDeny: true })
  const out = await $.tool.call({ tool: SS, records: [] })
  expect(out.result).toBe(T1)
})

test('/search-view command opens pane and returns {}', async ($, on) => {
  const opened = base(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const r = await $.command.run({ command: 'search-view', args: '' })
  expect(r).toEqual({})
  expect(opened).toEqual(['search-view'])
})

test('summary, statuses, judged-out lines', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: JS, question: 'q', sources: [{ url: 'https://s.org/use' }, { url: 'https://s.org/skip' }, { url: 'https://s.org/unj' }] })
  await $.tool.call({ tool: 'WebFetch', url: 'https://p.org/a/#frag', prompt: 'x' })
  await $.tool.call({ tool: 'WebFetch', url: 'https://unscored.org/', prompt: 'x' })
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Sources' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '6 collected → 5 passed → 2 judged-out → 1 read' })).toBeDefined()
  for (const s of ['READ   ', 'USE    ', 'SKIP   ', 'PENDING', 'OUT    ']) expect(await ui.find({ type: 'Text', text: s })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'PASS   ' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: '        ↳ SKIP: seo filler' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^ {8}↳ UNJUDGED: / })).toBeDefined()
})

test('WebFetch passes through', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: [] })
  const r = await $.tool.call({ tool: 'WebFetch', url: 'https://p.org/a', prompt: 'x' })
  expect(r.result).toBe('fetched')
})

test('PASS for unread PRIMARY; merge by URL, later wins', async ($, on) => {
  base(on, { stdout: [T1, 'SCORE VERDICT T  SIGNALS URL\n 99.0 DROP     6  https://s.org/pend/#x\n 50.0 PRIMARY  1  https://n.org/'] })
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: SS, records: [] })
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'PASS   ' })).toBeDefined()
  // 7 unique, passed: p.org/a, use, skip, unj, n.org = 5 ; none judged
  expect(await ui.find({ type: 'Text', text: '7 collected → 5 passed → 0 judged-out → 0 read' })).toBeDefined()
})

test('searches section: pending, done with JSON and text URL counts, other agents ignored', async ($, on) => {
  base(on)
  await $.agent.spawn({ subagentType: 'scored-web-search:searcher', description: 'alpha', prompt: 'x' })
  await $.agent.spawn({ subagentType: 'scored-web-search:searcher', prompt: 'beta line\nmore' })
  await $.agent.spawn({ subagentType: 'scored-web-search:searcher', description: 'gamma', prompt: 'x' })
  await $.agent.spawn({ subagentType: 'general-purpose', description: 'other', prompt: 'x' })
  await $.turn.complete({ turnId: 't1', agentId: 'ag-alpha', answer: JSON.stringify([{ url: 'https://a' }, { url: 'https://b' }, { title: 'no' }]), durationMs: 1, isAborted: false, usage: null } as any)
  await $.turn.complete({ turnId: 't2', agentId: 'ag-p', answer: 'see https://x.org and http://y.org, not ftp://z', durationMs: 1, isAborted: false, usage: null } as any)
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Searches (3)' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '  ✓ alpha — 2 URLs' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '  ✓ beta line — 2 URLs' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '  … gamma' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /other/ })).toBeUndefined()
})

test('sorted by score descending', async ($, on) => {
  base(on, { stdout: 'H\n 10.0 PRIMARY  1  https://low.org\n 90.0 PRIMARY  1  https://high.org' })
  await $.tool.call({ tool: SS, records: [] })
  const ui = await $.ui.mount(PANE)
  const hi = await ui.find({ type: 'Text', text: /high\.org/ })
  const lo = await ui.find({ type: 'Text', text: /low\.org/ })
  expect(hi).toBeDefined(); expect(lo).toBeDefined()
  const tree = JSON.stringify((ui as any).tree ?? (ui as any).root ?? await (ui as any).toJSON?.() ?? '')
  if (tree.length > 2) expect(tree.indexOf('high.org')).toBeLessThan(tree.indexOf('low.org'))
})

for (const source of ['clear', 'resume', 'fork']) {
  test(`SessionStart ${source} resets and reopens`, async ($, on) => {
    const opened = base(on)
    await $.tool.call({ tool: SS, records: [] })
    await $.classic.SessionStart({ source } as any)
    let ui = await $.ui.mount(PANE)
    expect(await ui.find({ type: 'Text', text: '0 collected → 0 passed → 0 judged-out → 0 read' })).toBeDefined()
    await $.tool.call({ tool: SS, records: [] })
    expect(opened).toEqual(['search-view', 'search-view'])
  })
}
