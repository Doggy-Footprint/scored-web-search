// Run state shown in the side pane. Pure data and functions: no mods API here.

const VERDICTS = 'PRIMARY|SUPPORT|SKIM|WEAK|DROP|BLOCKED'
const ROW = new RegExp('^\\s*([\\d.]+)\\s+(' + VERDICTS + ')\\s+\\S+\\s+(?:(.*?)\\s+)?(https?://\\S+)\\s*$')

export function emptyRun() {
  return { queries: [], rounds: [], sources: [], judged: {}, read: {} }
}

// A round is one score_sources call. `parent` names an earlier round's label; an unknown or missing one makes it a root.
export function addRound(run, { label, parent, count }) {
  const id = run.rounds.length + 1
  const up = parent ? [...run.rounds].reverse().find((r) => r.label === parent) : undefined
  run.rounds.push({ id, label: label || 'Search ' + id, parent: up ? up.id : null, count })
  return id
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

// Merging keeps later rounds (Step 4 re-search, Step 5 follow-up) in one view: a later score wins, the first round keeps the source.
export function mergeSources(run, rows, round) {
  const byUrl = new Map(run.sources.map((s) => [normUrl(s.url), s]))
  for (const r of rows) {
    const prev = byUrl.get(normUrl(r.url))
    byUrl.set(normUrl(r.url), { ...r, round: prev && prev.round != null ? prev.round : round })
  }
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

function sourceRows(el, run, s, indent) {
  const { Box, Text } = el
  const st = status(run, s)
  const j = run.judged[normUrl(s.url)]
  const rows = [Box({
    flexDirection: 'row',
    columnGap: 1,
    children: [
      Text({ children: [indent] }),
      Text({ ...STATUS_STYLE[st], children: [(st === 'READ' ? '✓ READ' : st).padEnd(7)] }),
      Text({ dimColor: st === 'OUT', children: [s.score.toFixed(0).padStart(3) + ' ' + s.verdict.padEnd(7)] }),
      Text({ dimColor: st === 'OUT', wrap: 'truncate-middle', children: [s.url] }),
    ],
  })]
  if (j && j.verdict !== 'USE') rows.push(Text({ dimColor: true, wrap: 'truncate-end', children: [indent + '        ↳ ' + j.verdict + ': ' + j.reason] }))
  return rows
}

function roundRows(el, run, round, depth) {
  const { Text } = el
  const indent = '   '.repeat(depth)
  const rows = [Text({ bold: true, wrap: 'truncate-end', children: [indent + '▾ ' + (depth ? 'Follow-up: ' : 'Search: ') + round.label + '  ' + round.count + ' URLs'] })]
  for (const s of run.sources.filter((x) => x.round === round.id)) rows.push(...sourceRows(el, run, s, indent + '   '))
  for (const child of run.rounds.filter((r) => r.parent === round.id)) rows.push(...roundRows(el, run, child, depth + 1))
  return rows
}

export function buildPane(el, run, onClose) {
  const { Box, Text, Button } = el
  const rows = []
  rows.push(Box({
    flexDirection: 'row',
    justifyContent: 'space-between',
    children: [
      Text({ dimColor: true, children: [summary(run)] }),
      Button({ key: 'close', role: 'dismiss', onPress: onClose, children: ['Close'] }),
    ],
  }))
  const pending = run.queries.filter((q) => q.urls == null)
  for (const q of pending) rows.push(Text({ wrap: 'truncate-end', color: 'yellow', children: ['… searching: ' + q.label] }))
  for (const r of run.rounds.filter((x) => x.parent == null)) rows.push(...roundRows(el, run, r, 0))
  // Sources scored before rounds existed (or with no round) still show.
  const orphans = run.sources.filter((s) => !run.rounds.some((r) => r.id === s.round))
  for (const s of orphans) rows.push(...sourceRows(el, run, s, ''))
  return Box({ flexDirection: 'column', children: rows })
}
