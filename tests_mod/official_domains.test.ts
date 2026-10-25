import { expect, test } from 'claude-code/testing'

const SS = 'mcp__scored-web-search__score_sources'
const FSREAD = (on: any) => on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))

const valuesOf = (argv: string[], flag: string) =>
  argv.flatMap((a, i) => (a === flag ? [argv[i + 1]] : []))

function capture(on: any) {
  const argvs: string[][] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($: any, e: any) => { argvs.push(e.argv); return { value: { exitCode: 0, stdout: 'OUT', stderr: '' } } })
  return argvs
}

test('score_sources schema declares officialDomains as an array of strings', async ($, on) => {
  FSREAD(on)
  const tools: any[] = []
  on('session.start', () => ({ cwd: '/tmp/x' }))
  on('tool.register', ($, e) => { tools.push(e); return { value: undefined } })
  on('agent.register', () => ({ value: undefined }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const ss = tools.find(t => JSON.stringify(t).includes('score_sources'))
  expect(ss).toBeTruthy()
  const s = JSON.stringify(ss)
  expect(s).toContain('officialDomains')
  // Locate the property definition wherever the schema nests it.
  let found: any = null
  const walk = (o: any) => {
    if (!o || typeof o !== 'object' || found) return
    if (o.properties && o.properties.officialDomains) { found = o.properties.officialDomains; return }
    for (const v of Object.values(o)) walk(v)
  }
  walk(ss)
  expect(found).toBeTruthy()
  expect(found.type).toBe('array')
  expect(found.items.type).toBe('string')
})

test('officialDomains become repeated --official-domain flags in order', async ($, on) => {
  FSREAD(on)
  const argvs = capture(on)
  await $.tool.call({ tool: SS, records: [], mode: 'official-docs', officialDomains: ['a.dev', 'b.io'] })
  const a = argvs[0]
  expect(valuesOf(a, '--official-domain')).toEqual(['a.dev', 'b.io'])
  const i = a.indexOf('--official-domain')
  expect(a.slice(i, i + 4)).toEqual(['--official-domain', 'a.dev', '--official-domain', 'b.io'])
})

test('no officialDomains (absent or empty) passes no --official-domain', async ($, on) => {
  FSREAD(on)
  const argvs = capture(on)
  await $.tool.call({ tool: SS, records: [], mode: 'official-docs' })
  await $.tool.call({ tool: SS, records: [], mode: 'official-docs', officialDomains: [] })
  expect(argvs.length).toBe(2)
  for (const a of argvs) expect(a).not.toContain('--official-domain')
})
