import { expect, test } from 'claude-code/testing'
import { htmlToText, parseVerdict } from './register.js'
import { emptyRun, mergeSources } from './view.js'

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }

test('score_sources returns the script table', async ($, on) => {
  const argvs: string[][] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($, e) => {
    argvs.push(e.argv)
    return { value: { exitCode: 0, stdout: 'TABLE', stderr: '' } }
  })
  const out = await $.tool.call({ tool: 'mcp__scored-web-search__score_sources', records: [{ url: 'https://a.org' }], mode: 'news' })
  expect(out.result).toBe('TABLE')
  expect(argvs[0]).toContain('news')
})

test('score_sources reports script failure', async ($, on) => {
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({ value: { exitCode: 2, stdout: '', stderr: 'boom' } }))
  const out = await $.tool.call({ tool: 'mcp__scored-web-search__score_sources', records: [] })
  expect(out.result).toMatch(/failed.*boom/)
})

test('judge_support returns verdict lines only', async ($, on) => {
  on('http.fetch', ($, e) => (e.url.includes('dead')
    ? { value: { ok: false, status: 404, headers: {}, text: '' } }
    : { value: { ok: true, status: 200, headers: { 'content-type': 'text/html' }, text: '<p>SECRET PAGE BODY</p>' } }))
  on('model.complete', () => ({ value: { isAnswered: true, text: 'SKIP|thin rewrite', usage: USAGE } }))
  const out = await $.tool.call({
    tool: 'mcp__scored-web-search__judge_support',
    question: 'q',
    sources: [{ url: 'https://a.org' }, { url: 'https://dead.org' }],
  })
  expect(out.result).toBe('https://a.org | SKIP | thin rewrite\nhttps://dead.org | UNJUDGED | fetch status 404')
  expect(out.result).not.toContain('SECRET')
})

test('helpers', async () => {
  expect(parseVerdict('USE|has data')).toBe('USE | has data')
  expect(parseVerdict('maybe')).toBe('UNJUDGED | unparseable judge reply')
  expect(htmlToText('<script>x</script><b>a</b>&amp; b')).toBe('a & b')
})

const PANE = {
  plugin: 'scored-web-search', component: 'Pane', requestId: 'search-view', surface: 'terminal',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Scored search', isFocused: false, bodyColumns: 70, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

const TABLE = [
  'SCORE VERDICT T  SIGNALS URL',
  ' 98.0 PRIMARY  1  cit=10 https://p.org/a',
  ' 70.0 SUPPORT  3  https://s.org/use',
  ' 66.0 SUPPORT  3  y2025 https://s.org/skip',
  ' 14.0 DROP     6  https://d.org/x',
].join('\n')

test('side view tracks score, judge, and read', async ($, on) => {
  const opened: string[] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: TABLE, stderr: '' } }))
  on('ui.open', ($, e) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('http.fetch', () => ({ value: { ok: true, status: 200, headers: {}, text: '<p>body</p>' } }))
  on('model.complete', ($, e) => ({ value: { isAnswered: true, text: e.prompt.includes('/skip') ? 'SKIP|seo filler' : 'USE|data', usage: USAGE } }))
  on('tool.call', () => ({ result: 'fetched' }))

  await $.tool.call({ tool: 'mcp__scored-web-search__score_sources', records: [] })
  await $.tool.call({ tool: 'mcp__scored-web-search__score_sources', records: [] })
  expect(opened).toEqual(['search-view'])
  await $.tool.call({ tool: 'mcp__scored-web-search__judge_support', question: 'q', sources: [{ url: 'https://s.org/use' }, { url: 'https://s.org/skip' }] })
  await $.tool.call({ tool: 'WebFetch', url: 'https://p.org/a/', prompt: 'x' })

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: '4 collected → 3 passed → 1 judged-out → 1 read' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /SKIP: seo filler/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^READ/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^OUT/ })).toBeDefined()
})

test('sources merge by normalized url and sort by score', async () => {
  const run = emptyRun()
  mergeSources(run, [{ score: 50, verdict: 'SKIM', signals: '', url: 'https://a.org/x' }, { score: 90, verdict: 'PRIMARY', signals: '', url: 'https://b.org' }])
  mergeSources(run, [{ score: 95, verdict: 'PRIMARY', signals: '', url: 'https://a.org/x/#f' }])
  expect(run.sources.map((s) => s.score)).toEqual([95, 90])
})
