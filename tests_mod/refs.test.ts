import { expect, test } from 'claude-code/testing'
// The harness forbids node:fs; each reference file is stubbed with a distinct sentinel so the test proves
// the mod forwards the file's content verbatim. Byte-identity with disk is checked in tests/test_skill_layout_independent.py.
const real = (n: string) => '<<SENTINEL ' + n + '>>\n  line two with  spacing\n'
const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const JS = 'mcp__scored-web-search__judge_support'
const SS = 'mcp__scored-web-search__score_sources'

function fsFromDisk(on: any, paths: string[]) {
  on('fs.read', ($: any, e: any) => {
    paths.push(e.path)
    const m = /\/skills\/scored-web-search\/references\/([^/]+)$/.exec(e.path)
    if (!m) return { deny: 'unexpected read ' + e.path }
    return { value: real(m[1]) }
  })
}

test('searcher agent prompt is exactly searcher-prompt.md read from plugin root', async ($, on) => {
  const paths: string[] = [], agents: any[] = []
  fsFromDisk(on, paths)
  on('session.start', () => ({ cwd: '/tmp/x' }))
  on('tool.register', () => ({ value: undefined }))
  on('command.register', () => ({ value: undefined }))
  on('agent.register', ($: any, e: any) => { agents.push(e); return { value: undefined } })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const a = agents.find(x => x.name === 'searcher')
  expect(a.prompt).toBe(real('searcher-prompt.md'))
  expect(paths.some(p => p.endsWith('/skills/scored-web-search/references/searcher-prompt.md'))).toBe(true)
  expect(paths.every(p => /^\/.+\/skills\/scored-web-search\/references\/[^/]+$/.test(p))).toBe(true)
})

test('judge prompt first block is the rubric (cached, byte-identical), page after', async ($, on) => {
  const paths: string[] = [], calls: any[] = []
  fsFromDisk(on, paths)
  on('http.fetch', () => ({ value: { ok: true, status: 200, headers: { 'content-type': 'text/html' }, text: '<p>PAGEBODY</p>' } }))
  on('model.complete', ($: any, e: any) => { calls.push(e); return { value: { isAnswered: true, text: 'USE|ok', usage: USAGE } } })
  await $.tool.call({ tool: JS, question: 'Q?', sources: [{ url: 'https://a.x' }, { url: 'https://b.x' }] })
  expect(calls.length).toBe(2)
  for (const c of calls) {
    const blocks = c.promptBlocks ?? c.prompt
    expect(blocks[0].cache).toBe(true)
    expect(blocks[0].text).toBe(real('judge-rubric.md'))
    expect(blocks[0].text).not.toContain('PAGEBODY')
    expect(JSON.stringify(blocks.slice(1))).toContain('PAGEBODY')
  }
  expect(paths.filter(p => p.endsWith('/skills/scored-web-search/references/judge-rubric.md')).length).toBeGreaterThan(0)
  expect(paths.every(p => /^\/.+\/skills\/scored-web-search\/references\/judge-rubric\.md$/.test(p))).toBe(true)
})

test('score_sources runs scorer inside the skill folder', async ($, on) => {
  const argvs: string[][] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($: any, e: any) => { argvs.push(e.argv); return { value: { exitCode: 0, stdout: 'SCORE VERDICT T SIGNALS URL', stderr: '' } } })
  await $.tool.call({ tool: SS, records: [] })
  expect(argvs[0][1]).toMatch(/^\/.+\/skills\/scored-web-search\/scripts\/srcscore\.py$/)
  expect(argvs[0][1]).not.toMatch(/\/scripts\/scripts\//)
})
