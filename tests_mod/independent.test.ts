import { expect, test } from 'claude-code/testing'
import { htmlToText, parseVerdict } from '../hooks/register.js'

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const SS = 'mcp__scored-web-search__score_sources'
const JS = 'mcp__scored-web-search__judge_support'
const FSREAD = (on: any) => on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))
const html = (text: string, ct = 'text/html') => ({ value: { ok: true, status: 200, headers: { 'content-type': ct }, text } })

test('session.start registers tools and searcher agent', async ($, on) => {
  FSREAD(on)
  const tools: any[] = []
  const agents: any[] = []
  on('session.start', () => ({ cwd: '/tmp/x' }))
  on('tool.register', ($, e) => { tools.push(e); return { value: undefined } })
  on('agent.register', ($, e) => { agents.push(e); return { value: undefined } })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const names = JSON.stringify(tools)
  expect(names).toContain('score_sources')
  expect(names).toContain('judge_support')
  const s = JSON.stringify(agents)
  expect(s).toContain('searcher')
  expect(s).toContain('haiku')
  expect(s).toContain('WebSearch')
})

test('score_sources writes JSON and runs script with mode and field', async ($, on) => {
  FSREAD(on)
  const writes: any[] = []
  const argvs: string[][] = []
  on('fs.write', ($, e) => { writes.push(e); return { value: undefined } })
  on('process.run', ($, e) => { argvs.push(e.argv); return { value: { exitCode: 0, stdout: 'OUT', stderr: '' } } })
  const recs = [{ url: 'https://a.org', title: 'A', date: '2024-01-01' }]
  const out = await $.tool.call({ tool: SS, records: recs, mode: 'news', field: 'cs' })
  expect(out.result).toBe('OUT')
  const w = writes[0]
  const path = w.path ?? w.file ?? w.filePath
  expect(path).toMatch(/\/\.tmp\/urls-.*\.json$/)
  expect(JSON.parse(w.content ?? w.data ?? w.text)).toEqual(recs)
  const a = argvs[0]
  expect(a[0]).toBe('python3')
  expect(a[1]).toMatch(/\/scripts\/srcscore\.py$/)
  expect(a.slice(2)).toEqual(['--in', path, '--mode', 'news', '--field', 'cs'])
})

test('score_sources defaults missing/invalid mode to academic, no field', async ($, on) => {
  FSREAD(on)
  const argvs: string[][] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($, e) => { argvs.push(e.argv); return { value: { exitCode: 0, stdout: 'x', stderr: '' } } })
  await $.tool.call({ tool: SS, records: [] })
  await $.tool.call({ tool: SS, records: [], mode: 'bogus' })
  for (const a of argvs) {
    expect(a[a.indexOf('--mode') + 1]).toBe('academic')
    expect(a).not.toContain('--field')
  }
})

test('score_sources nonzero exit', async ($, on) => {
  FSREAD(on)
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: 'err' } }))
  const out = await $.tool.call({ tool: SS, records: [] })
  expect(out.result).toMatch(/failed|could not run/)
  expect(out.result).toContain('do not fall back to unscored reading')
})

test('score_sources process.run rejects', async ($, on) => {
  FSREAD(on)
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({ deny: 'nope' }))
  const out = await $.tool.call({ tool: SS, records: [] })
  expect(out.result).toMatch(/failed|could not run/)
  expect(out.result).toContain('do not fall back to unscored reading')
})

test('judge_support UNJUDGED cases and ordering', async ($, on) => {
  FSREAD(on)
  on('http.fetch', ($, e) => {
    if (e.url.includes('404')) return { value: { ok: false, status: 503, headers: {}, text: '' } }
    if (e.url.includes('pdfct')) return html('stuff', 'application/pdf')
    if (e.url.includes('pdfbody')) return html('%PDF-1.7 ...', 'application/octet-stream')
    if (e.url.includes('throw')) return { deny: 'net' }
    if (e.url.includes('empty')) return html('<script>a()</script><style>b{}</style>   ')
    return html('<p>content</p>')
  })
  on('model.complete', () => ({ value: { isAnswered: true, text: 'USE|ok', usage: USAGE } }))
  const urls = ['https://404.x', 'https://pdfct.x', 'https://pdfbody.x', 'https://throw.x', 'https://empty.x', 'https://good.x']
  const out = await $.tool.call({ tool: JS, question: 'q', sources: urls.map(url => ({ url })) })
  expect(out.result).toBe([
    'https://404.x | UNJUDGED | fetch status 503',
    'https://pdfct.x | UNJUDGED | pdf not supported',
    'https://pdfbody.x | UNJUDGED | pdf not supported',
    'https://throw.x | UNJUDGED | fetch failed',
    'https://empty.x | UNJUDGED | empty page',
    'https://good.x | USE | ok',
  ].join('\n'))
})

test('judge_support model call shape, truncation, no page text leak', async ($, on) => {
  FSREAD(on)
  const calls: any[] = []
  const body = 'Z'.repeat(30000)
  on('http.fetch', () => html(`<p>${body}</p>`))
  on('model.complete', ($, e) => { calls.push(e); return { value: { isAnswered: true, text: 'preamble\nSKIP|weak', usage: USAGE } } })
  const out = await $.tool.call({ tool: JS, question: 'Q?', sources: [{ url: 'https://a.x', title: 'T' }] })
  expect(out.result).toBe('https://a.x | SKIP | weak')
  expect(out.result).not.toContain('ZZZZ')
  const c = calls[0]
  expect(c.model).toBe('haiku')
  expect(Array.isArray(c.promptBlocks)).toBe(true)
  expect(c.promptBlocks[0].cache).toBe(true)
  const all = JSON.stringify(c.promptBlocks)
  const zs = (all.match(/Z+/g) || []).reduce((m: number, s: string) => Math.max(m, s.length), 0)
  expect(zs).toBe(20000)
})

test('judge_support unparseable and unavailable', async ($, on) => {
  FSREAD(on)
  on('http.fetch', () => html('<p>hi</p>'))
  on('model.complete', ($, e) => (JSON.stringify(e.prompt).includes('unavail')
    ? { value: { isAnswered: false, text: '', usage: USAGE } }
    : { value: { isAnswered: true, text: 'I think maybe', usage: USAGE } }))
  const a = await $.tool.call({ tool: JS, question: 'q', sources: [{ url: 'https://a.x' }] })
  expect(a.result).toBe('https://a.x | UNJUDGED | unparseable judge reply')
  const b = await $.tool.call({ tool: JS, question: 'unavail', sources: [{ url: 'https://b.x' }] })
  expect(b.result).toBe('https://b.x | UNJUDGED | judge unavailable')
})

test('htmlToText and parseVerdict', async () => {
  expect(htmlToText('<nav>menu</nav><style>p{}</style><p>a&lt;b &gt; c&quot;d&#39;</p>\n\n  <div>e&nbsp;f</div>')).toMatch(/^a<b > c"d' e.f$/)
  expect(parseVerdict('junk\nUSE|good data')).toBe('USE | good data')
  expect(parseVerdict('SKIP|bad')).toBe('SKIP | bad')
  expect(parseVerdict('nothing')).toBe('UNJUDGED | unparseable judge reply')
})
