import { atom, read, update } from 'claude-code'
import { addRound, buildPane, countUrls, emptyRun, mergeSources, normUrl, parseJudgeLines, parseScoreTable } from './view.js'

const PANE = 'search-view'
const SEARCHER = 'scored-web-search:searcher'
const PAGE_CHARS = 20000
const JUDGE_CONCURRENCY = 4
const MODES = ['academic', 'non-academic', 'community-opinion', 'news', 'official-docs']

// The skill folder is the single origin of the scorer and the prompts; the skill-only path reads the same files.
const SKILL_DIR = '/skills/scored-web-search'
// Read whole and unchanged, so the rubric stays byte-identical across calls and forms the cached prefix.
const ref = ($, name) => $.fs.read($.plugin.root + SKILL_DIR + '/references/' + name)

// Session-only history in host state, so a write redraws the pane and a mod reload keeps it; reset on /clear, /resume, /branch.
const RUN = atom({ plugin: 'scored-web-search', key: 'run' }, emptyRun())
const edit = ($, fn) => update($, RUN, (r) => { const n = JSON.parse(JSON.stringify(r)); fn(n); return n })

export function register(on, options) {
  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'score_sources',
      description: 'Scored web search step 2: score collected search records with srcscore.py. Returns the score table (SCORE VERDICT T SIGNALS URL).',
      inputSchema: {
        type: 'object',
        properties: {
          records: {
            type: 'array',
            items: {
              type: 'object',
              properties: { url: { type: 'string' }, title: { type: 'string' }, date: { type: 'string' } },
              required: ['url'],
            },
          },
          mode: { type: 'string', enum: MODES },
          field: { type: 'string' },
          round: { type: 'string', description: 'Short label for this search round, shown in the side view' },
          parentRound: { type: 'string', description: 'For a re-search or follow-up: the round label it came from' },
        },
        required: ['records'],
      },
    })
    await $.tool.register({
      name: 'judge_support',
      description: 'Scored web search step 2.5: a cheap model reads each SUPPORT source and returns `url | USE/SKIP/UNJUDGED | reason`. Page text is never returned. Only pass SUPPORT-verdict sources.',
      inputSchema: {
        type: 'object',
        properties: {
          question: { type: 'string', description: 'The research question the sources must serve' },
          sources: {
            type: 'array',
            items: { type: 'object', properties: { url: { type: 'string' }, title: { type: 'string' } }, required: ['url'] },
          },
        },
        required: ['question', 'sources'],
      },
    })
    await $.agent.register({
      name: 'searcher',
      description: 'Scored web search step 1: runs web searches and returns URL/title/date JSON only.',
      prompt: await ref($, 'searcher-prompt.md'),
      tools: ['WebSearch'],
      model: 'haiku',
      omitClaudeMd: true,
    })
    await $.command.register({ name: 'search-view', description: 'Open the scored web search side view' })
    return next(e)
  })

  on('tool.call', { tool: 'mcp__scored-web-search__score_sources' }, async ($, e) => {
    const mode = MODES.includes(e.mode) ? e.mode : 'academic'
    const dir = $.plugin.root + '/.tmp'
    const infile = dir + '/urls-' + Date.now() + '.json'
    await $.fs.write(infile, JSON.stringify(e.records || []))
    const argv = ['python3', $.plugin.root + SKILL_DIR + '/scripts/srcscore.py', '--in', infile, '--mode', mode]
    if (e.field) argv.push('--field', e.field)
    try {
      const r = await $.process.run(argv, { timeoutMs: 120000 })
      if (r.exitCode !== 0) return { result: 'srcscore.py failed (exit ' + r.exitCode + '): ' + r.stderr.trim() + '\nStop: do not fall back to unscored reading.' }
      const rows = parseScoreTable(r.stdout)
      await edit($, (run) => mergeSources(run, rows, addRound(run, { label: e.round, parent: e.parentRound, count: (e.records || []).length })))
      return { result: r.stdout }
    } catch (err) {
      return { result: 'srcscore.py could not run: ' + String(err && err.message || err) + '\nStop: do not fall back to unscored reading.' }
    }
  }).catch(($, e, next) => ({ result: 'score_sources failed: ' + next.error.message + '\nStop: do not fall back to unscored reading.' }))

  on('tool.call', { tool: 'mcp__scored-web-search__judge_support' }, async ($, e, next) => {
    const sources = e.sources || []
    const lines = new Array(sources.length)
    let cursor = 0
    const worker = async () => {
      while (cursor < sources.length && !next.signal.aborted) {
        const i = cursor++
        lines[i] = sources[i].url + ' | ' + (await judgeOne($, sources[i], e.question, options.judgeModel || 'haiku'))
      }
    }
    const workers = []
    for (let k = 0; k < Math.min(JUDGE_CONCURRENCY, sources.length); k++) workers.push(worker())
    await Promise.all(workers)
    const result = lines.filter(Boolean).join('\n')
    const judged = parseJudgeLines(result)
    await edit($, (run) => Object.assign(run.judged, judged))
    return { result }
  }).catch(($, e, next) => ({ result: 'judge_support failed: ' + next.error.message + '\nJudge unavailable: read SUPPORT sources as before and note it.' }))

  on('agent.spawn', { subagentType: SEARCHER }, async ($, e, next) => {
    const r = await next(e)
    try {
      if (r.agentId) {
        await edit($, (run) => run.queries.push({ agentId: r.agentId, label: e.description || String(e.prompt).split('\n')[0].slice(0, 80), urls: null }))
      }
      await showPane($)
    } catch (err) {}
    return r
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId && (await read($, RUN)).queries.some((x) => x.agentId === e.agentId)) {
      const urls = countUrls(e.answer)
      await edit($, (run) => { run.queries.find((x) => x.agentId === e.agentId).urls = urls })
    }
    return next(e)
  })

  on('tool.call', { tool: 'WebFetch' }, async ($, e, next) => {
    try {
      const key = normUrl(e.url)
      if ((await read($, RUN)).sources.some((s) => normUrl(s.url) === key)) await edit($, (run) => { run.read[key] = true })
    } catch (err) {}
    return next(e)
  })

  on('command.run', { command: 'search-view' }, async ($) => {
    await $.ui.open({ id: PANE, title: 'Scored search', closeOnEscape: true })
    return {}
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    return buildPane($.ui.resolve(e), await read($, RUN), () => { void $.ui.close({ id: PANE }) })
  })

  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    await update($, RUN, () => emptyRun())
    return next(e)
  })
}

// Opens on each searcher spawn while closed. An unasked open is drawn only on wide terminals, so say how to see it otherwise.
async function showPane($) {
  if ((await $.ui.panes()).some((p) => p.id === PANE)) return
  const r = await $.ui.open({ id: PANE, title: 'Scored search', closeOnEscape: true })
  if (r && r.isPlaced === false) $.ui.toast('Scored search: run /search-view to open the side view')
}

export async function judgeOne($, source, question, model) {
  let page
  try {
    const res = await $.http.fetch(source.url, { headers: { Accept: 'text/html,text/plain' } })
    if (!res.ok) return 'UNJUDGED | fetch status ' + res.status
    const type = (res.headers && (res.headers['content-type'] || res.headers['Content-Type'])) || ''
    if (/pdf/i.test(type) || /^%PDF/.test(res.text)) return 'UNJUDGED | pdf not supported'
    page = htmlToText(res.text).slice(0, PAGE_CHARS)
  } catch (err) {
    return 'UNJUDGED | fetch failed'
  }
  if (!page) return 'UNJUDGED | empty page'
  const r = await $.model.complete({
    model,
    prompt: [
      { text: await ref($, 'judge-rubric.md'), cache: true },
      { text: 'Question: ' + question + '\nTitle: ' + (source.title || '') + '\nURL: ' + source.url + '\n\n<page>\n' + page + '\n</page>' },
    ],
    maxTokens: 100,
    timeoutMs: 60000,
  })
  if (!r.isAnswered) return 'UNJUDGED | judge unavailable'
  return parseVerdict(r.text)
}

export function parseVerdict(text) {
  const m = /^\s*(USE|SKIP)\s*\|\s*(.*)$/m.exec(text || '')
  if (!m) return 'UNJUDGED | unparseable judge reply'
  return m[1] + ' | ' + m[2].trim().slice(0, 200)
}

export function htmlToText(html) {
  return String(html || '')
    .replace(/<(script|style|noscript|svg|nav|footer|header)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
}
