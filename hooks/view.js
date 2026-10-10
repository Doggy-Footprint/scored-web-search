// Run state shown in the side pane. Pure data and functions: no mods API here.

const VERDICTS = 'PRIMARY|SUPPORT|SKIM|WEAK|DROP|BLOCKED'
const ROW = new RegExp('^\\s*([\\d.]+)\\s+(' + VERDICTS + ')\\s+\\S+\\s+(?:(.*?)\\s+)?(https?://\\S+)\\s*$')

export function emptyRun() {
  return { queries: [], sources: [], judged: {}, read: {} }
}

export function normUrl(url) {
  return String(url || '').replace(/#.*$/, '').replace(/\/+$/, '')
}

export function parseScoreTable(stdout) {
  const rows = []
  for (const line of String(stdout || '').split('\n')) {
    const m = ROW.exec(line)
    if (m) rows.push({ score: Number(m[1]), verdict: m[2], signals: (m[3] || '').trim(), url: m[4] })
  }
  return rows
}

export function parseJudgeLines(text) {
  const out = {}
  for (const line of String(text || '').split('\n')) {
    const m = /^(\S+)\s*\|\s*(USE|SKIP|UNJUDGED)\s*\|\s*(.*)$/.exec(line)
    if (m) out[normUrl(m[1])] = { verdict: m[2], reason: m[3] }
  }
  return out
}

export function countUrls(answer) {
  try {
    const parsed = JSON.parse(String(answer).replace(/^[\s\S]*?(\[[\s\S]*\])[\s\S]*$/, '$1'))
    if (Array.isArray(parsed)) return parsed.filter((r) => r && r.url).length
  } catch (err) {}
  return (String(answer || '').match(/https?:\/\/\S+/g) || []).length
}

// Merging keeps later rounds (Step 4 re-search, Step 5 follow-up) in one view.
export function mergeSources(run, rows) {
  const byUrl = new Map(run.sources.map((s) => [normUrl(s.url), s]))
  for (const r of rows) byUrl.set(normUrl(r.url), r)
  run.sources = [...byUrl.values()].sort((a, b) => b.score - a.score)
}

export function status(run, s) {
  const key = normUrl(s.url)
  if (run.read[key]) return 'READ'
  const j = run.judged[key]
  if (j) return j.verdict === 'USE' ? 'USE' : 'SKIP'
  if (s.verdict === 'PRIMARY') return 'PASS'
  if (s.verdict === 'SUPPORT') return 'PENDING'
  return 'OUT'
}

export function summary(run) {
  const passed = run.sources.filter((s) => s.verdict === 'PRIMARY' || s.verdict === 'SUPPORT')
  const judgedOut = passed.filter((s) => {
    const j = run.judged[normUrl(s.url)]
    return j && j.verdict !== 'USE'
  }).length
  const read = run.sources.filter((s) => run.read[normUrl(s.url)]).length
  return run.sources.length + ' collected → ' + passed.length + ' passed → ' + judgedOut + ' judged-out → ' + read + ' read'
}

const STATUS_STYLE = {
  READ: { color: 'green', bold: true },
  USE: { color: 'green' },
  PASS: { color: 'green' },
  PENDING: { color: 'yellow' },
  SKIP: { color: 'red' },
  OUT: { dimColor: true },
}

export function buildPane(el, run) {
  const { Box, Text } = el
  const rows = []
  rows.push(Text({ bold: true, children: ['Searches (' + run.queries.length + ')'] }))
  for (const q of run.queries) {
    rows.push(Text({ wrap: 'truncate-end', children: ['  ' + (q.urls == null ? '… ' : '✓ ') + q.label + (q.urls == null ? '' : ' — ' + q.urls + ' URLs')] }))
  }
  rows.push(Text({ children: [' '] }))
  rows.push(Text({ bold: true, children: ['Sources'] }))
  rows.push(Text({ dimColor: true, children: [summary(run)] }))
  for (const s of run.sources) {
    const st = status(run, s)
    const j = run.judged[normUrl(s.url)]
    rows.push(Box({
      flexDirection: 'row',
      columnGap: 1,
      children: [
        Text({ ...STATUS_STYLE[st], children: [st.padEnd(7)] }),
        Text({ dimColor: st === 'OUT', children: [s.score.toFixed(0).padStart(3) + ' ' + s.verdict.padEnd(7)] }),
        Text({ dimColor: st === 'OUT', wrap: 'truncate-middle', children: [s.url] }),
      ],
    }))
    if (j && j.verdict !== 'USE') rows.push(Text({ dimColor: true, wrap: 'truncate-end', children: ['        ↳ ' + j.verdict + ': ' + j.reason] }))
  }
  return Box({ flexDirection: 'column', children: rows })
}
