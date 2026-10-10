export type Query = { agentId: string; label: string; urls: number | null }
export type Round = { id: number; label: string; parent: number | null; count: number }
export type Source = { score: number; verdict: string; signals: string; url: string; round?: number | null }
export type Run = {
  queries: Query[]
  rounds: Round[]
  sources: Source[]
  judged: Record<string, { verdict: string; reason: string }>
  read: Record<string, boolean>
}

declare module 'claude-code' {
  interface PluginState {
    'scored-web-search': { run: Run }
  }
}
