import { expect, test } from 'claude-code/testing'

const SS = 'mcp__scored-web-search__score_sources'
const FSREAD = (on: any) => on('fs.read', ($: any, e: any) => ({ value: 'stub:' + e.path }))

// Returns the values following each occurrence of `flag` in argv.
const valuesOf = (argv: string[], flag: string) =>
  argv.flatMap((a, i) => (a === flag ? [argv[i + 1]] : []))

function capture(on: any) {
  const argvs: string[][] = []
  on('fs.write', () => ({ value: undefined }))
  on('process.run', ($: any, e: any) => { argvs.push(e.argv); return { value: { exitCode: 0, stdout: 'OUT', stderr: '' } } })
  return argvs
}

test('score_sources schema declares docVersions and docVariants', async ($, on) => {
  FSREAD(on)
  const tools: any[] = []
  on('session.start', () => ({ cwd: '/tmp/x' }))
  on('tool.register', ($, e) => { tools.push(e); return { value: undefined } })
  on('agent.register', () => ({ value: undefined }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/tmp/x' })
  const ss = JSON.stringify(tools.find(t => JSON.stringify(t).includes('score_sources')))
  expect(ss).toContain('docVersions')
  expect(ss).toContain('docVariants')
})

test('score_sources passes docVersions and docVariants as --doc-version/--doc-variant', async ($, on) => {
  FSREAD(on)
  const argvs = capture(on)
  await $.tool.call({
    tool: SS, records: [], mode: 'official-docs',
    docVersions: { 'pypi.org': '0.7.5', 'nextjs.org': '14' },
    docVariants: { 'nextjs.org': 'app' },
  })
  const a = argvs[0]
  expect(a[a.indexOf('--mode') + 1]).toBe('official-docs')
  expect(valuesOf(a, '--doc-version').sort()).toEqual(['nextjs.org=14', 'pypi.org=0.7.5'])
  expect(valuesOf(a, '--doc-variant')).toEqual(['nextjs.org=app'])
})

test('score_sources without doc options passes no doc flags', async ($, on) => {
  FSREAD(on)
  const argvs = capture(on)
  await $.tool.call({ tool: SS, records: [], mode: 'news' })
  await $.tool.call({ tool: SS, records: [], docVersions: {}, docVariants: {} })
  for (const a of argvs) {
    expect(a).not.toContain('--doc-version')
    expect(a).not.toContain('--doc-variant')
  }
})

test('score_sources docVariants alone, multiple domains', async ($, on) => {
  FSREAD(on)
  const argvs = capture(on)
  await $.tool.call({ tool: SS, records: [], docVariants: { 'nextjs.org': 'pages', 'example.org': 'x' } })
  const a = argvs[0]
  expect(a).not.toContain('--doc-version')
  expect(valuesOf(a, '--doc-variant').sort()).toEqual(['example.org=x', 'nextjs.org=pages'])
})
