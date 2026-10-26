import { expect, test } from 'claude-code/testing'
// Written from the specification, not from the implementation:
// - a fetch that rejects gives `<url> | UNJUDGED | fetch failed: <message>`, message whitespace-collapsed,
//   trimmed, cut to <= 200 chars, always one line;
// - when there is >= 1 source and every line is such a fetch rejection, one final line
//   `Judge unavailable: read SUPPORT sources as before and note it.` is appended;
// - any USE/SKIP or any other UNJUDGED reason (status, pdf, empty, unparseable, model unavailable) suppresses it;
// - zero sources: no extra line;
// - the extra line is not recorded as a judged source in the pane;
// - a throwing tool still ends with the same line.
// A `{ deny: reason }` fetch stub rejects with message 'scored-web-search: $.http.fetch: <reason>'
// (as observed by tests_mod/independent.test.ts).

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const SS = 'mcp__scored-web-search__score_sources'
const JS = 'mcp__scored-web-search__judge_support'
const UNAVAIL = 'Judge unavailable: read SUPPORT sources as before and note it.'
const PREFIX = 'scored-web-search: $.http.fetch: '
const html = (text: string, ct = 'text/html') => ({ value: { ok: true, status: 200, headers: { 'content-type': ct }, text } })

// URL routing: rej* -> fetch rejects (deny 'net'), s503 -> status 503, pdf -> pdf, empty -> empty page,
// use/skip/garbage/unavail -> model reply USE / SKIP / unparseable / not answered.
function stubs(on: any, opts: { deny?: (url: string) => string | undefined } = {}) {
  on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))
  on('fs.write', () => ({ value: undefined }))
  on('http.fetch', ($: any, e: any) => {
    const u: string = e.url
    const d = opts.deny?.(u)
    if (d !== undefined) return { deny: d }
    if (u.includes('rej')) return { deny: 'net' }
    if (u.includes('s503')) return { value: { ok: false, status: 503, headers: {}, text: '' } }
    if (u.includes('pdf')) return html('%PDF-1.7 ...', 'application/pdf')
    if (u.includes('empty')) return html('<script>a()</script><style>b{}</style>   ')
    if (u.includes('skip')) return html('<p>page skip</p>')
    if (u.includes('garbage')) return html('<p>page garbage</p>')
    if (u.includes('unavail')) return html('<p>page unavail</p>')
    return html('<p>page use</p>')
  })
  on('model.complete', ($: any, e: any) => {
    const p = JSON.stringify(e.prompt ?? '') + JSON.stringify(e.promptBlocks ?? '')
    if (p.includes('page unavail')) return { value: { isAnswered: false, text: '', usage: USAGE } }
    if (p.includes('page garbage')) return { value: { isAnswered: true, text: 'I think maybe', usage: USAGE } }
    if (p.includes('page skip')) return { value: { isAnswered: true, text: 'SKIP|weak', usage: USAGE } }
    return { value: { isAnswered: true, text: 'USE|ok', usage: USAGE } }
  })
}
const call = ($: any, urls: string[]) => $.tool.call({ tool: JS, question: 'q', sources: urls.map((url) => ({ url })) })
const rejLine = (url: string, reason = 'net') => `${url} | UNJUDGED | fetch failed: ${PREFIX}${reason}`

// 1 + 2: single and multiple all-rejected
test('S1 single rejected source: line plus Judge unavailable line', async ($, on) => {
  stubs(on)
  const out = await call($, ['https://rej1.x'])
  expect(out.result).toBe([rejLine('https://rej1.x'), UNAVAIL].join('\n'))
})

test('S2 all sources rejected: lines in input order then exactly one final line', async ($, on) => {
  stubs(on)
  const urls = ['https://rej1.x/a', 'https://rej2.x/b', 'https://rej3.x/c', 'https://rej4.x/d', 'https://rej5.x/e']
  const out = await call($, urls)
  expect(out.result).toBe([...urls.map((u) => rejLine(u)), UNAVAIL].join('\n'))
  expect(out.result.split('\n').filter((l: string) => l === UNAVAIL).length).toBe(1)
})

// 1: message normalisation
test('S3 multi-line / tabbed message is collapsed to one line and trimmed', async ($, on) => {
  stubs(on, { deny: (u) => (u.includes('rej') ? 'line one\n\n   line\ttwo \r\n  three   \n ' : undefined) })
  const out = await call($, ['https://rej1.x'])
  const lines = out.result.split('\n')
  expect(lines.length).toBe(2)
  expect(lines[0]).toBe(`https://rej1.x | UNJUDGED | fetch failed: ${PREFIX}line one line two three`)
  expect(lines[1]).toBe(UNAVAIL)
})

test('S4 long message is cut to at most 200 characters', async ($, on) => {
  const reason = 'abcdefghij'.repeat(40) // 400 chars, no whitespace
  stubs(on, { deny: (u) => (u.includes('rej') ? reason : undefined) })
  const out = await call($, ['https://rej1.x'])
  const lines = out.result.split('\n')
  expect(lines.length).toBe(2)
  const head = 'https://rej1.x | UNJUDGED | fetch failed: '
  expect(lines[0].startsWith(head)).toBe(true)
  const msg = lines[0].slice(head.length)
  expect(msg.length).toBeLessThanOrEqual(200)
  expect(msg).toBe((PREFIX + reason).slice(0, 200))
  expect(lines[1]).toBe(UNAVAIL)
})

test('S5 long multi-line message: collapsed before the cut, still single line', async ($, on) => {
  const reason = Array.from({ length: 60 }, (_, i) => 'w' + i).join('\n\n\t ')
  stubs(on, { deny: (u) => (u.includes('rej') ? reason : undefined) })
  const out = await call($, ['https://rej1.x', 'https://rej2.x'])
  const lines = out.result.split('\n')
  expect(lines.length).toBe(3)
  const collapsed = (PREFIX + reason).replace(/\s+/g, ' ').trim()
  const expected = collapsed.slice(0, 200)
  // Collapsing first means the cut keeps 200 chars of collapsed text; avoid the trailing-space edge.
  expect(expected.endsWith(' ')).toBe(false)
  expect(lines[0]).toBe('https://rej1.x | UNJUDGED | fetch failed: ' + expected)
  expect(lines[1]).toBe('https://rej2.x | UNJUDGED | fetch failed: ' + expected)
  expect(lines[2]).toBe(UNAVAIL)
})

// The test engine skips a throwing hook, so $.http.fetch then rejects with the engine's own
// 'no implementation for http.fetch' error rather than the thrown message; only the line shape is checked.
test('S6 http.fetch handler that throws: fetch still rejects, single line + extra line', async ($, on) => {
  on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))
  on('fs.write', () => ({ value: undefined }))
  on('http.fetch', () => { throw new Error('boom\nsecond line') })
  on('model.complete', () => ({ value: { isAnswered: true, text: 'USE|ok', usage: USAGE } }))
  const out = await call($, ['https://rej1.x'])
  const lines = out.result.split('\n')
  expect(lines.length).toBe(2)
  expect(lines[0]).toMatch(/^https:\/\/rej1\.x \| UNJUDGED \| fetch failed: \S/)
  expect(lines[0].length - 'https://rej1.x | UNJUDGED | fetch failed: '.length).toBeLessThanOrEqual(200)
  expect(lines[1]).toBe(UNAVAIL)
})

// 3: mixes suppress the extra line
const MIX: Array<[string, string, string]> = [
  ['USE', 'https://use.x', 'https://use.x | USE | ok'],
  ['SKIP', 'https://skip.x', 'https://skip.x | SKIP | weak'],
  ['fetch status', 'https://s503.x', 'https://s503.x | UNJUDGED | fetch status 503'],
  ['pdf', 'https://pdf.x', 'https://pdf.x | UNJUDGED | pdf not supported'],
  ['empty page', 'https://empty.x', 'https://empty.x | UNJUDGED | empty page'],
  ['unparseable', 'https://garbage.x', 'https://garbage.x | UNJUDGED | unparseable judge reply'],
  ['model unavailable', 'https://unavail.x', 'https://unavail.x | UNJUDGED | judge unavailable'],
]
for (const [name, url, line] of MIX) {
  test(`S7 rejection + ${name}: no extra line`, async ($, on) => {
    stubs(on)
    const out = await call($, ['https://rej1.x', url, 'https://rej2.x'])
    expect(out.result).toBe([rejLine('https://rej1.x'), line, rejLine('https://rej2.x')].join('\n'))
    expect(out.result).not.toContain('Judge unavailable')
  })
}

test('S8 all sources non-rejection UNJUDGED: no extra line', async ($, on) => {
  stubs(on)
  const urls = ['https://s503.x', 'https://pdf.x', 'https://empty.x', 'https://garbage.x', 'https://unavail.x']
  const out = await call($, urls)
  expect(out.result).toBe(MIX.slice(2).map(([, , l]) => l).join('\n'))
  expect(out.result).not.toContain('Judge unavailable')
})

test('S9 single model-unavailable source alone: no extra line', async ($, on) => {
  stubs(on)
  const out = await call($, ['https://unavail.x'])
  expect(out.result).toBe('https://unavail.x | UNJUDGED | judge unavailable')
})

test('S10 all USE/SKIP: no extra line', async ($, on) => {
  stubs(on)
  const out = await call($, ['https://use.x', 'https://skip.x'])
  expect(out.result).toBe('https://use.x | USE | ok\nhttps://skip.x | SKIP | weak')
})

// 4
test('S11 zero sources: no extra line', async ($, on) => {
  stubs(on)
  const out = await $.tool.call({ tool: JS, question: 'q', sources: [] })
  expect(out.result).not.toContain('Judge unavailable')
  expect(out.result).not.toContain('judge_support failed')
})

// 6
test('S12 tool throws: result still ends with the Judge unavailable line', async ($, on) => {
  stubs(on)
  const out = await $.tool.call({ tool: JS, question: 'q', sources: [null] } as any)
  expect(out.result.startsWith('judge_support failed: ')).toBe(true)
  expect(out.result.endsWith('\n' + UNAVAIL)).toBe(true)
})

// 5: pane / run state
const PANE = {
  plugin: 'scored-web-search', component: 'Pane', requestId: 'search-view', surface: 'terminal',
  viewport: { columns: 200, rows: 60 },
  props: { title: 'Scored search', isFocused: false, bodyColumns: 120, placement: 'dock', scroll: { offset: 0, bodyRows: 50 }, view: {} },
} as const
const T1 = [
  'SCORE VERDICT T  SIGNALS URL',
  ' 98.0 PRIMARY  1  cit=10 https://p.org/a',
  ' 70.0 SUPPORT  3  https://rej1.org/a',
  ' 66.0 SUPPORT  3  https://rej2.org/b',
  ' 60.0 SUPPORT  3  https://rej3.org/c',
  ' 14.0 DROP     6  https://d.org/x',
].join('\n')
function paneStubs(on: any) {
  const files: Record<string, string> = {}
  on('fs.read', ($: any, e: any) => ({ value: e.path in files ? files[e.path] : 'stub:' + e.path }))
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($: any, e: any) => {
    const k = e.argv.indexOf('--full-out')
    if (k >= 0) files[e.argv[k + 1]] = T1
    return { value: { exitCode: 0, stdout: T1, stderr: '' } }
  })
  on('http.fetch', () => ({ deny: 'net' }))
  on('model.complete', () => ({ value: { isAnswered: true, text: 'USE|ok', usage: USAGE } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({ value: [] }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.close', () => ({ value: undefined }))
  on('tool.call', () => ({ result: 'fetched' }))
}

test('S13 pane: per-source fetch-failure verdicts recorded, extra line is not a judged source', async ($, on) => {
  paneStubs(on)
  await $.tool.call({ tool: SS, records: [] })
  const out = await call($, ['https://rej1.org/a', 'https://rej2.org/b', 'https://rej3.org/c'])
  expect(out.result.endsWith('\n' + UNAVAIL)).toBe(true)
  const ui = await $.ui.mount(PANE)
  // 5 rows collected, 4 non-DROP passed, 3 judged-out = exactly the 3 judged sources
  expect(await ui.find({ type: 'Text', text: '5 collected → 4 passed → 3 judged-out → 0 read' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /↳ UNJUDGED: fetch failed: / })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Judge unavailable/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /read SUPPORT sources as before/ })).toBeUndefined()
  const tree = JSON.stringify((ui as any).tree ?? (ui as any).root ?? '')
  if (tree.length > 2) {
    expect(tree).not.toContain('Judge unavailable')
    expect((tree.match(/↳ UNJUDGED: fetch failed: /g) || []).length).toBe(3)
  }
})

test('S14 pane: extra line does not change judged-out count vs a non-rejection run', async ($, on) => {
  paneStubs(on)
  await $.tool.call({ tool: SS, records: [] })
  await call($, ['https://rej1.org/a'])
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: '5 collected → 4 passed → 1 judged-out → 0 read' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Judge unavailable/ })).toBeUndefined()
})
