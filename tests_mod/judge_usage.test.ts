import { expect, test } from 'claude-code/testing'
// Written from the specification, not from the implementation: after judging, judge_support writes one
// usage record at $.plugin.root + '/.tmp/judge-usage-<number>.json' via $.fs.write, JSON
// {question, model, calls:[{url, line, usage}]}. `line` is the verdict text after `url | ` (or null),
// `usage` is the model.complete usage (null when no model call happened), `model` is the configured
// judgeModel (default 'haiku'). A throwing fs.write must not change the tool result, and the returned
// text carries no usage. fs.write is always stubbed here, so nothing lands in the repo's .tmp/.

const JS = 'mcp__scored-web-search__judge_support'
const USAGE_A = { input_tokens: 1234, output_tokens: 17, cache_read_input_tokens: 900, cache_creation_input_tokens: 0 }
const USAGE_B = { input_tokens: 4321, output_tokens: 23, cache_read_input_tokens: 0, cache_creation_input_tokens: 800 }
const FSREAD = (on: any) => on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))
const html = (text: string, ct = 'text/html') => ({ value: { ok: true, status: 200, headers: { 'content-type': ct }, text } })
const bodyOf = (w: any) => w.text ?? w.content ?? w.data
const pathOf = (w: any) => w.path ?? w.file ?? w.filePath
const usageWrites = (writes: any[]) => writes.filter((w) => /\/\.tmp\/judge-usage-[^/]*\.json$/.test(pathOf(w)))

// a.x -> USE (usage A), b.x -> SKIP (usage B), bad.x -> fetch status 503 (no model call), pdf.x -> pdf (no model call)
function stubs(on: any, writes: any[], models: string[] = []) {
  FSREAD(on)
  on('fs.write', ($: any, e: any) => { writes.push(e); return { value: undefined } })
  on('http.fetch', ($: any, e: any) => {
    if (e.url.includes('bad.x')) return { value: { ok: false, status: 503, headers: {}, text: '' } }
    if (e.url.includes('pdf.x')) return html('%PDF-1.4', 'application/pdf')
    if (e.url.includes('b.x')) return html('<p>page b</p>')
    return html('<p>page a</p>')
  })
  on('model.complete', ($: any, e: any) => {
    models.push(e.model)
    const isB = JSON.stringify(e.promptBlocks ?? e.prompt).includes('page b')
    return { value: isB ? { isAnswered: true, text: 'SKIP|off topic', usage: USAGE_B }
                        : { isAnswered: true, text: 'USE|has data', usage: USAGE_A } }
  })
}

const SOURCES = [{ url: 'https://a.x/1', title: 'A' }, { url: 'https://bad.x/2' }, { url: 'https://b.x/3' }, { url: 'https://pdf.x/4' }]
const EXPECTED_RESULT = [
  'https://a.x/1 | USE | has data',
  'https://bad.x/2 | UNJUDGED | fetch status 503',
  'https://b.x/3 | SKIP | off topic',
  'https://pdf.x/4 | UNJUDGED | pdf not supported',
].join('\n')

test('writes exactly one usage file under plugin root .tmp with a numeric name', async ($, on) => {
  const writes: any[] = []
  stubs(on, writes)
  await $.tool.call({ tool: JS, question: 'Q?', sources: SOURCES })
  const u = usageWrites(writes)
  expect(u.length).toBe(1)
  expect(pathOf(u[0])).toMatch(/^\/.+\/\.tmp\/judge-usage-\d+\.json$/)
  expect(pathOf(u[0])).not.toMatch(/\/\.tmp\/.*\/\.tmp\//)
})

test('usage record has question, default model haiku, and one call per source in order', async ($, on) => {
  const writes: any[] = []
  stubs(on, writes)
  await $.tool.call({ tool: JS, question: 'Does X cause Y?', sources: SOURCES })
  const doc = JSON.parse(bodyOf(usageWrites(writes)[0]))
  expect(Object.keys(doc).sort()).toEqual(['calls', 'model', 'question'])
  expect(doc.question).toBe('Does X cause Y?')
  expect(doc.model).toBe('haiku')
  expect(doc.calls.map((c: any) => c.url)).toEqual(SOURCES.map((s) => s.url))
  for (const c of doc.calls) expect(Object.keys(c).sort()).toEqual(['line', 'url', 'usage'])
})

test('usage is the model.complete usage, null when no model call happened', async ($, on) => {
  const writes: any[] = []
  stubs(on, writes)
  await $.tool.call({ tool: JS, question: 'q', sources: SOURCES })
  const calls = JSON.parse(bodyOf(usageWrites(writes)[0])).calls
  expect(calls[0].usage).toEqual(USAGE_A)
  expect(calls[1].usage).toBe(null)   // fetch failed
  expect(calls[2].usage).toEqual(USAGE_B)
  expect(calls[3].usage).toBe(null)   // pdf
})

test('line is the verdict text after "url | "', async ($, on) => {
  const writes: any[] = []
  stubs(on, writes)
  await $.tool.call({ tool: JS, question: 'q', sources: SOURCES })
  const calls = JSON.parse(bodyOf(usageWrites(writes)[0])).calls
  expect(calls.map((c: any) => c.line)).toEqual([
    'USE | has data',
    'UNJUDGED | fetch status 503',
    'SKIP | off topic',
    'UNJUDGED | pdf not supported',
  ])
})

test('model is the configured judgeModel', { options: { judgeModel: 'luna' } } as any, async ($: any, on: any) => {
  const writes: any[] = [], models: string[] = []
  stubs(on, writes, models)
  await $.tool.call({ tool: JS, question: 'q', sources: SOURCES })
  expect(models.length).toBe(2)
  expect(models.every((m) => m === 'luna')).toBe(true)
  expect(JSON.parse(bodyOf(usageWrites(writes)[0])).model).toBe('luna')
})

test('returned text is unchanged: verdict lines only, no usage', async ($, on) => {
  const writes: any[] = []
  stubs(on, writes)
  const out = await $.tool.call({ tool: JS, question: 'q', sources: SOURCES })
  expect(out.result).toBe(EXPECTED_RESULT)
  expect(out.result).not.toContain('usage')
  expect(out.result).not.toContain('1234')
  expect(out.result).not.toContain('input_tokens')
})

test('a throwing fs.write does not change the tool result', async ($, on) => {
  FSREAD(on)
  let attempted = 0
  on('fs.write', () => { attempted++; return { deny: 'read-only filesystem' } })
  on('http.fetch', ($: any, e: any) => {
    if (e.url.includes('bad.x')) return { value: { ok: false, status: 503, headers: {}, text: '' } }
    if (e.url.includes('pdf.x')) return html('%PDF-1.4', 'application/pdf')
    if (e.url.includes('b.x')) return html('<p>page b</p>')
    return html('<p>page a</p>')
  })
  on('model.complete', ($: any, e: any) => (JSON.stringify(e.promptBlocks ?? e.prompt).includes('page b')
    ? { value: { isAnswered: true, text: 'SKIP|off topic', usage: USAGE_B } }
    : { value: { isAnswered: true, text: 'USE|has data', usage: USAGE_A } }))
  const out = await $.tool.call({ tool: JS, question: 'q', sources: SOURCES })
  expect(attempted).toBeGreaterThan(0)
  expect(out.result).toBe(EXPECTED_RESULT)
  expect(out.result).not.toContain('judge_support failed')
})
