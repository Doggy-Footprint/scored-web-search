#!/usr/bin/env python3
"""toolresult.py AGENT_ID TOOL OUT [N] -- copy the verbatim result of TOOL (WebSearch|WebFetch) from a subagent transcript.

N picks the N-th call (0-based, in transcript order); the default is the last one."""
import sys, json, glob, os
aid, tool, out = sys.argv[1:4]
n = int(sys.argv[4]) if len(sys.argv) > 4 else -1
hits = glob.glob(os.path.expanduser(f'~/.claude/projects/**/agent-{aid}.jsonl'), recursive=True)
if not hits:
    sys.exit(f'no transcript for {aid}')
uses, res = {}, []
for l in open(hits[0]):
    d = json.loads(l)
    c = d.get('message', {}).get('content')
    if not isinstance(c, list):
        continue
    for b in c:
        if b.get('type') == 'tool_use' and b.get('name') == tool:
            uses[b['id']] = b.get('input')
        if b.get('type') == 'tool_result' and b.get('tool_use_id') in uses:
            t = b['content'] if isinstance(b['content'], str) else '\n'.join(x.get('text', '') for x in b['content'] if isinstance(x, dict))
            res.append({'input': uses[b['tool_use_id']], 'result': t})
if not res:
    sys.exit(f'no {tool} result in {hits[0]}')
if n >= len(res):
    sys.exit(f'only {len(res)} {tool} results in {hits[0]}')
r = res[n]
open(out, 'w').write(f"INPUT: {json.dumps(r['input'], ensure_ascii=False)}\n\n=== RESULT ===\n{r['result']}")
print(f"{len(r['result'])} chars -> {out}")
