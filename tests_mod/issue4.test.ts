import { expect, test } from 'claude-code/testing'

// Independent tests for issue 4: batches in score_sources, compact + --full-out, searcher foreground,
// and the Agent result swap that keeps searcher records out of the main context.

const SS = 'mcp__scored-web-search__score_sources'
const SEARCHER = 'scored-web-search:searcher'
const PANE = {
  plugin: 'scored-web-search', component: 'Pane', requestId: 'search-view', surface: 'terminal',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Scored search', isFocused: false, bodyColumns: 70, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

const COMPACT = [
  'SCORE VERDICT T  SIGNALS                     URL',
  ' 98.0 PRIMARY  1  cit=10                      https://p.org/a',
  '-- 3 sources | PRIMARY 1 | SUPPORT 0 | SKIM 1 | WEAK 0 | DROP 1 | BLOCKED 0',
].join('\n')
const FULL = [
  'SCORE VERDICT T  SIGNALS                     URL',
  ' 98.0 PRIMARY  1  cit=10                      https://p.org/a',
  ' 50.0 SKIM     3  date-unknown                https://skim.org/hidden',
  ' 14.0 DROP     6  unregistered                https://drop.org/hidden',
  '-- 3 sources | 1 citable | 1 dropped',
].join('\n')

type Env = { files: Record<string, string>, writes: Record<string, string>, argvs: string[][], spawns: any[], agentCalls: any[] }

function setup(on: any, opts: { files?: Record<string, string>, batches?: Record<string, any[]>, agentResult?: any } = {}): Env {
  const env: Env = { files: { ...(opts.files ?? {}) }, writes: {}, argvs: [], spawns: [], agentCalls: [] }
  on('fs.read', ($: any, e: any) => {
    if (e.path in env.files) return { value: env.files[e.path] }
    for (const [id, recs] of Object.entries(opts.batches ?? {})) if (e.path.endsWith('/.tmp/batch-' + id + '.json')) return { value: JSON.stringify(recs) }
    if (e.path.includes('/.tmp/batch-')) throw new Error('ENOENT: ' + e.path)
    return { value: '' }
  })
  on('fs.write', ($: any, e: any) => { env.files[e.path] = e.content ?? e.data ?? e.text; env.writes[e.path] = env.files[e.path]; return { value: undefined } })
  on('process.run', ($: any, e: any) => {
    env.argvs.push(e.argv)
    const k = e.argv.indexOf('--full-out')
    if (k >= 0) env.files[e.argv[k + 1]] = FULL
    return { value: { exitCode: 0, stdout: COMPACT, stderr: '' } }
  })
  on('tool.call', ($: any, e: any) => {
    env.agentCalls.push(e)
    return opts.agentResult ?? { result: 'fetched' }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({ value: [] }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.close', () => ({ value: undefined }))
  on('agent.spawn', ($: any, e: any) => { env.spawns.push(e); return { model: 'haiku', agentId: 'ag-1' } })
  on('turn.complete', () => ({ text: '' }))
  return env
}

const writtenRecords = (env: Env) => {
  const k = Object.keys(env.writes).find((p) => /\/\.tmp\/urls-.*\.json$/.test(p))
  return k ? JSON.parse(env.writes[k]) : undefined
}

// ---------------------------------------------------------------- score_sources: batches

test('batches only (no records): batch records are scored', async ($, on) => {
  const recs = [{ url: 'https://b.org/1', title: 'B1' }, { url: 'https://b.org/2', title: 'B2', date: '2025-01-01' }]
  const env = setup(on, { batches: { abc_1: recs } })
  const out = await $.tool.call({ tool: SS, batches: ['abc_1'] } as any)
  expect(out.result).toBe(COMPACT)
  expect(env.argvs.length).toBe(1)
  expect(writtenRecords(env)).toEqual(recs)
})

test('batches and records are scored together', async ($, on) => {
  const b1 = [{ url: 'https://b.org/1', title: 'B1' }]
  const b2 = [{ url: 'https://c.org/1', title: 'C1' }]
  const env = setup(on, { batches: { one: b1, 'Two-2': b2 } })
  await $.tool.call({ tool: SS, records: [{ url: 'https://r.org/1' }], batches: ['one', 'Two-2'] } as any)
  const urls = (writtenRecords(env) as any[]).map((r) => r.url).sort()
  expect(urls).toEqual(['https://b.org/1', 'https://c.org/1', 'https://r.org/1'])
})

for (const bad of ['../etc', 'a/b', 'a.b', '', 'a b', 'x;rm']) {
  test('invalid batch id ' + JSON.stringify(bad) + ' stops without scoring', async ($, on) => {
    const env = setup(on)
    const out = await $.tool.call({ tool: SS, batches: [bad] } as any)
    expect(String(out.result)).toContain('do not fall back to unscored reading')
    expect(String(out.result).toLowerCase()).toMatch(/batch/)
    expect(env.argvs.length).toBe(0)
  })
}

test('missing batch file stops without scoring', async ($, on) => {
  const env = setup(on)
  const out = await $.tool.call({ tool: SS, records: [{ url: 'https://r.org/1' }], batches: ['nope'] } as any)
  expect(String(out.result)).toContain('do not fall back to unscored reading')
  expect(String(out.result)).toContain('nope')
  expect(env.argvs.length).toBe(0)
})

// ---------------------------------------------------------------- compact + full-out

test('scorer runs with --format compact --full-out .tmp/score-*.txt; result is stdout', async ($, on) => {
  const env = setup(on)
  const out = await $.tool.call({ tool: SS, records: [{ url: 'https://p.org/a' }] })
  const a = env.argvs[0]
  expect(a[a.indexOf('--format') + 1]).toBe('compact')
  expect(a.indexOf('--full-out')).toBeGreaterThan(0)
  expect(a[a.indexOf('--full-out') + 1]).toMatch(/\/\.tmp\/score-[^/]*\.txt$/)
  expect(out.result).toBe(COMPACT)
  expect(String(out.result)).not.toContain('skim.org')
})

test('pane is built from the full-out file: SKIM/DROP rows appear', async ($, on) => {
  setup(on)
  await $.tool.call({ tool: SS, records: [{ url: 'https://p.org/a' }], round: 'r1' })
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /skim\.org\/hidden/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /drop\.org\/hidden/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /p\.org\/a/ })).toBeDefined()
})

// ---------------------------------------------------------------- foreground spawn

test('searcher spawn forced to foreground', async ($, on) => {
  const env = setup(on)
  await $.agent.spawn({ subagentType: SEARCHER, description: 'd', prompt: 'x', background: true } as any)
  expect(env.spawns.length).toBe(1)
  expect(env.spawns[0].background).toBe(false)
})

test('other spawns keep their background flag', async ($, on) => {
  const env = setup(on)
  await $.agent.spawn({ subagentType: 'general-purpose', description: 'd', prompt: 'x', background: true } as any)
  expect(env.spawns[0].background).toBe(true)
})

// ---------------------------------------------------------------- Agent result swap

const ANSWER = 'Here you go:\n' + JSON.stringify([
  { url: 'https://secret-one.org/a', title: 'Secret Title One', date: '2025-02-03', snippet: 'leak' },
  { url: 'https://secret-two.org/b', title: 'Secret Title Two' },
])

test('searcher Agent result replaced by batch stub; records saved reduced', async ($, on) => {
  const env = setup(on, { agentResult: { result: { agentId: 'agX9', content: [{ type: 'text', text: ANSWER }] } } })
  const r: any = await $.tool.call({ tool: 'Agent', subagent_type: SEARCHER, prompt: 'x', description: 'd' } as any)
  const content = r.result.content
  expect(content.length).toBe(1)
  expect(content[0].type).toBe('text')
  expect(content[0].text).toBe('searcher batch agX9: 2 records saved (not shown). Pass batches: ["agX9"] to score_sources.')
  const s = JSON.stringify(r)
  for (const leak of ['secret-one.org', 'secret-two.org', 'Secret Title One', 'Secret Title Two', 'leak']) expect(s).not.toContain(leak)
  const k = Object.keys(env.writes).find((p) => p.endsWith('/.tmp/batch-agX9.json'))
  expect(k).toBeDefined()
  expect(JSON.parse(env.writes[k!])).toEqual([
    { url: 'https://secret-one.org/a', title: 'Secret Title One', date: '2025-02-03' },
    { url: 'https://secret-two.org/b', title: 'Secret Title Two' },
  ])
})

test('stub batch id round-trips into score_sources', async ($, on) => {
  const env = setup(on, { agentResult: { result: { agentId: 'agRT', content: [{ type: 'text', text: ANSWER }] } } })
  const r: any = await $.tool.call({ tool: 'Agent', subagent_type: SEARCHER, prompt: 'x' } as any)
  const id = /batches: \["([^"]+)"\]/.exec(r.result.content[0].text)![1]
  const out = await $.tool.call({ tool: SS, batches: [id] } as any)
  expect(out.result).toBe(COMPACT)
  expect((writtenRecords(env) as any[]).map((x) => x.url)).toEqual(['https://secret-one.org/a', 'https://secret-two.org/b'])
})

test('non-searcher Agent call passes through unchanged', async ($, on) => {
  const res = { result: { agentId: 'agG', content: [{ type: 'text', text: ANSWER }] } }
  const env = setup(on, { agentResult: res })
  const r: any = await $.tool.call({ tool: 'Agent', subagent_type: 'general-purpose', prompt: 'x' } as any)
  expect(r.result.content[0].text).toBe(ANSWER)
  expect(Object.keys(env.writes).some((p) => p.includes('/.tmp/batch-'))).toBe(false)
})
