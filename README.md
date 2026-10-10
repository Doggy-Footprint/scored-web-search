[posting](https://blog-steel-ten-13.vercel.app/posts/scored-web-search)
# Install

```bash
# Claude Code (mod + skill, v2.1.290+): install from the marketplace
claude plugin marketplace add Doggy-Footprint/claude-plugins
claude plugin install scored-web-search@doggy-footprint
```

Or from a clone:

```bash
git clone https://github.com/Doggy-Footprint/scored-web-search

# Claude Code (mod + skill, v2.1.290+): load as a plugin
claude --plugin-dir "$(realpath scored-web-search)"

# Claude Code (skill only): symlink the skill folder; it carries its own scorer
mkdir -p ~/.claude/skills
ln -s "$(realpath scored-web-search)/skills/scored-web-search" ~/.claude/skills/scored-web-search

# Codex (skill only, no mod tools): symlink the skill
mkdir -p ~/.codex/skills
ln -s "$(realpath scored-web-search)/skills/scored-web-search" ~/.codex/skills/scored-web-search
```

Mobile / claude.ai (skill only, no mod):

1. Download [scored-web-search.zip](https://github.com/Doggy-Footprint/scored-web-search/releases/latest/download/scored-web-search.zip). It is rebuilt on every change under `skills/scored-web-search/`.
2. In claude.ai, open Settings > Customize > Skills and upload the zip. The skill is account-wide, so the mobile app gets it too.
3. To update, download the zip again and re-upload. Auto-sync to the mobile app is not verified.

The mod tools (`judge_support`, `scored-web-search:searcher`) are not available on mobile; the skill falls back to general subagents. The scorer queries `api.openalex.org`, `api.semanticscholar.org`, `doi.org` and `hn.algolia.com`; in a cloud environment set the network to Custom (add these domains) or Full.

The mod adds `score_sources`, `judge_support` (SUPPORT judge, model set by `judgeModel` option, default `haiku`), and the `searcher` agent. Built against Claude Code 2.1.296. Skill only runs the same pipeline with general subagents and `scripts/srcscore.py`; the mod adds the side view (`/search-view`).

# Customization (policy)

Check `skills/scored-web-search/scripts/policy.json` and `skills/scored-web-search/scripts/modes/` to edit this skill permanently.

---

# Scored Web Search

AI chat, 특히 클로드에서 web search할 때, 결과물을 heuristic filter로 걸러내어 신뢰도가 낮은 정보를 차단하는 skill입니다.
달성 목표는 **Precision over Recall**, 모든 유용한 정보를 가져오지 못하더라도 오염된 정보를 걸러내는데 집중하는 스킬입니다.

## 왜 만들었나?

> 그거 아시나요? Claude는 검색할 때, 광고도 가리지 않는다는 사실!
> 그거 아시나요? Claude는 검색한 걸 읽을 때, 모든 자료를 평등하게 바라본다는 사실!
> 그거 아시나요? 가끔 검색하면 하나 밖에 안 나오는 medium 글을 대표 사례라고 가져온다는 사실!
> 그거 아니나요? low-quality 자료가 있으면 무시하는 게 아니라 context가 오염된다는 사실!

장난스럽게 말했지만, 고질적인 RAG 문제와, Context rot, context size 문제입니다.

## 어떻게 작동하나?

> LLM은 충분히 똑똑하며, precision이 recall보다 중요하다.

1. web search는 sub agent에게 맡겨 **토큰 소모**를 줄이고, **context rot**을 방지한다. (Task/Agent 도구가 없는 환경, 예: Codex는 main agent가 직접 검색하되 snippet은 읽지 않고 URL만 추출하는 fallback으로 동작한다)
2. 출처, 인용수, 저널, 좋아요 수, 별 수 등을 바탕으로 heuristic하게 점수를 매긴다. - 자세한 내용은 `SKILL.md`, `policy` 참고.
3. main agent(고비용, 고성능)은 선별된 소스를 읽고 리포트를 작성한다.

## 사용법

최상단 **Install**을 참고해주시기 바랍니다.

## 설계 결정

1. 재검색 - 검색 중 알게 된 용어가 원 질문의 미해결 부분을 조사하는 데 필요할 때 검색을 확장합니다. 자료가 적다는 것은 확장 사유가 아니며, 조건을 만족하는 새 키워드가 없으면 멈춥니다. 확장은 main agent가 읽은 본문에서 발견해 새 sub agent(KV cache 재활용)에 넘기는 경로로 일어납니다.
2. 토큰 소모 감소 - 11%🔻, 메인 에이전트가 읽는 소스가 줄어들고, sub agent의 토큰 소모도 많지 않아서 더 드라마틱 감소를 기대했는데, sub agent cold-start 비용이 30k이라 감소폭이 적었다.
3. `skills/scored-web-search/scripts/policy.json`에 단일 의존하는 점수 게산 - 개발이 쉬워진만큼 이 도구를 쓰는 사람이 AI의 도움을 받아 직접 수정하길 기대했다.
