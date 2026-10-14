import { expect, test } from 'claude-code/testing'
import { addRound, emptyRun } from '../hooks/view.js'

const SS = 'mcp__scored-web-search__score_sources'
const PANE = {
  plugin: 'scored-web-search', component: 'Pane', requestId: 'search-view', surface: 'terminal',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Scored search', isFocused: false, bodyColumns: 120, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const
const T = 'SCORE VERDICT T  SIGNALS URL\n 98.0 PRIMARY  1  cit=10 https://p.org/a'

function base(on: any, stdout: string[] = [T], argvs: string[][] = []) {
  const tools: any[] = []
  let i = 0
  on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($: any, e: any) => (argvs.push(e.argv), { value: { exitCode: 0, stdout: stdout[Math.min(i++, stdout.length - 1)], stderr: '' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({ value: [] }))
  on('ui.toast', () => ({ value: undefined }))
  on('tool.register', ($: any, e: any) => { tools.push(e); return { value: undefined } })
  on('agent.register', () => ({ value: undefined }))
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/tmp/x' }))
  return tools
}
const recs = (n: number) => Array.from({ length: n }, (_, k) => ({ url: 'https://r.org/' + k }))

async function texts(ui: any): Promise<string[]> {
  const all = await ui.findAll({ type: 'Text' })
  return all.map((t: any) => t.text)
}

// addRound
test('addRound stores queries array', () => {
  const run = emptyRun()
  addRound(run, { label: 'a', queries: ['q1', 'q2'], count: 1 })
  expect(run.rounds[0].queries).toEqual(['q1', 'q2'])
})

test('addRound: missing or non-array queries become []', () => {
  const run = emptyRun()
  addRound(run, { label: 'a', count: 1 })
  addRound(run, { label: 'b', queries: 'q1' as any, count: 1 })
  addRound(run, { label: 'c', queries: null as any, count: 1 })
  addRound(run, { label: 'd', queries: { 0: 'x' } as any, count: 1 })
  for (const r of run.rounds) expect(r.queries).toEqual([])
})

test('addRound drops empty/whitespace/non-string entries', () => {
  const run = emptyRun()
  addRound(run, { label: 'a', queries: ['q1', '', '  ', 3 as any, null as any, 'q2'], count: 1 })
  expect(run.rounds[0].queries).toEqual(['q1', 'q2'])
})

// schema
test('score_sources schema declares optional queries: string[]', async ($, on) => {
  const tools = base(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const ss = tools.find((t) => JSON.stringify(t).includes('score_sources'))
  const s = JSON.stringify(ss)
  const schema = ss.inputSchema ?? ss.schema ?? ss.parameters ?? ss.input_schema
  if (schema) {
    expect(schema.properties.queries.type).toBe('array')
    expect(schema.properties.queries.items.type).toBe('string')
    expect(schema.required ?? []).not.toContain('queries')
  } else {
    expect(s).toContain('"queries":{"type":"array","items":{"type":"string"}')
  }
})

// end-to-end pane
test('e2e: queries line directly below Search header', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: recs(2), round: 'llm eval', queries: ['llm eval benchmark', 'mmlu 2025'] })
  const ui = await $.ui.mount(PANE)
  const t = await texts(ui)
  const h = t.indexOf('▾ Search: llm eval  2 URLs')
  expect(h).toBeGreaterThan(-1)
  expect(t[h + 1]).toBe('   🔍 llm eval benchmark · mmlu 2025')
  const q = await ui.find({ type: 'Text', text: '   🔍 llm eval benchmark · mmlu 2025' })
  expect((q as any).props.dimColor).toBe(true)
})

test('e2e: no queries line when queries absent or empty', async ($, on) => {
  base(on)
  await $.tool.call({ tool: SS, records: recs(1), round: 'A' })
  await $.tool.call({ tool: SS, records: recs(1), round: 'B', queries: [] })
  await $.tool.call({ tool: SS, records: recs(1), round: 'C', queries: ['', ' '] })
  const ui = await $.ui.mount(PANE)
  const t = await texts(ui)
  expect(t.some((x) => x.includes('🔍'))).toBe(false)
})

test('e2e: follow-up queries line uses follow-up indent', async ($, on) => {
  base(on, ['H'])
  await $.tool.call({ tool: SS, records: recs(1), round: 'A', queries: ['a1'] })
  await $.tool.call({ tool: SS, records: recs(1), round: 'B', parentRound: 'A', queries: ['b1', 'b2'] })
  await $.tool.call({ tool: SS, records: recs(1), round: 'C', parentRound: 'B' })
  const ui = await $.ui.mount(PANE)
  const t = await texts(ui)
  const a = t.indexOf('▾ Search: A  1 URLs')
  expect(t[a + 1]).toBe('   🔍 a1')
  const b = t.indexOf('   ▾ Follow-up: B  1 URLs')
  expect(b).toBeGreaterThan(-1)
  expect(t[b + 1]).toBe('      🔍 b1 · b2')
  const c = t.indexOf('      ▾ Follow-up: C  1 URLs')
  expect(c).toBeGreaterThan(-1)
  expect((t[c + 1] ?? '').includes('🔍')).toBe(false)
})

test('e2e: queries do not change scorer argv', async ($, on) => {
  const argvs: string[][] = []
  base(on, [T], argvs)
  const out = await $.tool.call({ tool: SS, records: recs(1), mode: 'news', queries: ['x'] })
  expect(out.result).toBe(T)
  expect(argvs[0].join(' ')).not.toContain('x --')
  expect(argvs[0]).not.toContain('x')
})

// SKILL.md / mod.md checks live in tests/test_keywords_docs.py (the mod harness cannot load .md files).
