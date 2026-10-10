import { buildPane, countUrls, emptyRun, mergeSources, normUrl, parseJudgeLines, parseScoreTable } from './view.js'

const PANE = 'search-view'
const SEARCHER = 'scored-web-search:searcher'
const PAGE_CHARS = 20000
const JUDGE_CONCURRENCY = 4
const MODES = ['academic', 'non-academic', 'community-opinion', 'news', 'official-docs']

const SEARCHER_PROMPT = `You collect search results. Run the WebSearch tool for the queries you are given and return ONLY a JSON array of {"url", "title", "date"?} records.
- "date" is an ISO date, included only when the search result itself supplies the publication date. Never guess.
- No summaries, no snippets, no commentary. Never open pages.
- Return 20-30 records per sub-topic, deduplicated by URL.`

// Kept byte-identical across calls so it forms the cached prefix.
const RUBRIC = `You are a strict source judge for a research pipeline. A heuristic scorer rated the page below as SUPPORT: credible but not primary. Decide whether the main researcher should read it for the question.

Answer USE only when all hold:
1. The page substantively addresses the question (not a passing mention, index page, or paywall/login stub).
2. It carries its own evidence, data, or first-hand detail, or clearly attributes claims to named sources.
3. It is not marketing, SEO filler, or a thin rewrite of another article.

Otherwise answer SKIP.

Reply with exactly one line: USE|<reason up to 15 words> or SKIP|<reason up to 15 words>`

// Session-only history; reset on /clear, /resume, /branch.
let run = emptyRun()
let paneOpened = false

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
      prompt: SEARCHER_PROMPT,
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
    const argv = ['python3', $.plugin.root + '/scripts/srcscore.py', '--in', infile, '--mode', mode]
    if (e.field) argv.push('--field', e.field)
    try {
      const r = await $.process.run(argv, { timeoutMs: 120000 })
      if (r.exitCode !== 0) return { result: 'srcscore.py failed (exit ' + r.exitCode + '): ' + r.stderr.trim() + '\nStop: do not fall back to unscored reading.' }
      mergeSources(run, parseScoreTable(r.stdout))
      try { await showPane($) } catch (err) {}
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
    Object.assign(run.judged, parseJudgeLines(result))
    $.ui.invalidate('ui.render')
    return { result }
  }).catch(($, e, next) => ({ result: 'judge_support failed: ' + next.error.message + '\nJudge unavailable: read SUPPORT sources as before and note it.' }))

  on('agent.spawn', { subagentType: SEARCHER }, async ($, e, next) => {
    const r = await next(e)
    try {
      if (r.agentId) {
        run.queries.push({ agentId: r.agentId, label: e.description || String(e.prompt).split('\n')[0].slice(0, 80), urls: null })
        $.ui.invalidate('ui.render')
      }
    } catch (err) {}
    return r
  })

  on('turn.complete', async ($, e, next) => {
    const q = e.agentId && run.queries.find((x) => x.agentId === e.agentId)
    if (q) {
      q.urls = countUrls(e.answer)
      $.ui.invalidate('ui.render')
    }
    return next(e)
  })

  on('tool.call', { tool: 'WebFetch' }, async ($, e, next) => {
    try {
      const key = normUrl(e.url)
      if (run.sources.some((s) => normUrl(s.url) === key)) {
        run.read[key] = true
        $.ui.invalidate('ui.render')
      }
    } catch (err) {}
    return next(e)
  })

  on('command.run', { command: 'search-view' }, async ($) => {
    await $.ui.open({ id: PANE, title: 'Scored search' })
    return {}
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    return buildPane($.ui.resolve(e), run)
  })

  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    run = emptyRun()
    paneOpened = false
    return next(e)
  })
}

// Opens once per session on the first score; after the user closes it, /search-view reopens it.
async function showPane($) {
  if (paneOpened) return $.ui.invalidate('ui.render')
  paneOpened = true
  await $.ui.open({ id: PANE, title: 'Scored search' })
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
      { text: RUBRIC, cache: true },
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
