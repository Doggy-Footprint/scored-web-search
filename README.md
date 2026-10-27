포트폴리오: https://blog-steel-ten-13.vercel.app/posts/scored-web-search

# Scored Web Search

웹 검색 결과를 main agent가 읽기 전에 heuristic 점수로 걸러내는 skill입니다. **Precision over Recall**: 유용한 자료를 일부 놓치더라도 저품질 자료가 main context에 들어오지 않게 합니다.

> **현재는 `academic` mode 사용을 권합니다.** 다른 mode는 개선이 필요합니다([평가](#평가)).

# 동작 원리

```
1. Search  경량 subagent가 URL/title/date만 수집 (요약·snippet 없음)
2. Score   srcscore.py가 0-100점 + verdict 부여
2.5 Judge  경량 subagent가 SUPPORT만 열어보고 USE / SKIP / UNJUDGED 판정
3. Read    main agent는 PRIMARY + USE 판정 SUPPORT만 읽음
4. 재검색 (최대 2라운드)
5. 주장-근거 대조 → 6. 출처·점수 태그를 단 보고서, 끝에 단계별 실패를 적는 `Degraded:` 줄
```

- **점수** = 도메인 tier 기본점(88 / 74 / 60 / 46 / 32 / 14, 미등록은 32) ± 인용수, 게재 여부, GitHub star·HN 점수, 최신성, URL 경로 감점(SEO 경로, 구버전 문서 등).
- **verdict**: PRIMARY 78+ · SUPPORT 62-77 · SKIM 46-61 · WEAK 30-45 · DROP <30 · BLOCKED 0. main agent가 보는 건 PRIMARY와 SUPPORT 행, 그리고 verdict별 개수뿐입니다.
- **mode**: `academic`(기본), `non-academic`, `community-opinion`, `news`, `official-docs`. mode마다 도메인 tier와 신호 가중치가 다릅니다.
- 규칙 전체는 [`SKILL.md`](skills/scored-web-search/SKILL.md), 점수 정책은 [`policy.json`](skills/scored-web-search/scripts/policy.json)과 [`modes/`](skills/scored-web-search/scripts/modes/)에 있습니다.

# 사용법

어느 방법이든 같은 skill 폴더가 설치됩니다.

```bash
# Claude Code: plugin으로 설치
claude plugin marketplace add Doggy-Footprint/claude-plugins
claude plugin install scored-web-search@doggy-footprint

# Claude Code / Codex: clone 후 skill 폴더를 symlink
git clone https://github.com/Doggy-Footprint/scored-web-search
mkdir -p ~/.claude/skills
ln -s "$(realpath scored-web-search)/skills/scored-web-search" ~/.claude/skills/scored-web-search   # Codex: ~/.codex/skills/
```

- **claude.ai / mobile**: [scored-web-search.zip](https://github.com/Doggy-Footprint/scored-web-search/releases/latest/download/scored-web-search.zip)을 Settings > Customize > Skills에 업로드합니다. cloud 환경처럼 network가 제한된 곳에서는 scorer가 조회하는 `api.openalex.org`, `api.semanticscholar.org`, `api.github.com`, `hn.algolia.com`, `doi.org`를 허용해야 합니다. 막혀 있으면 skill은 점수를 매기지 않고 멈춥니다.
- **호출**: "scored web search로 ~ 조사해줘"처럼 요청하고, 필요하면 mode를 함께 적습니다. 보고서의 각 주장에는 `(Nature 2025, PRIMARY 91)` 형식의 태그가 붙습니다.
- **커스터마이징**: `policy.json`과 `modes/*.json`을 고친 뒤 `python3 scripts/check_policy.py`로 검증합니다.

# 평가

자세한 방법론은 [`policy-eval/METHODOLOGY.md`](policy-eval/METHODOLOGY.md)에 있습니다. pilot은 2026-10-24에 20문항(mode당 4문항)으로 돌렸습니다.

- **방법**: policy를 모르는 blind judge(Opus 5.5)가 직접 검색한 URL에 `useful` / `marginal` / `junk` 라벨을 붙이고, 이를 policy의 verdict와 비교합니다. plugin을 켠 실행과 끈 실행도 같은 질문으로 비교합니다. 보고서 자체의 정확도는 측정하지 않았습니다.
- **Policy**: 통과분의 useful 비율이 전체 풀의 20%에서 51%로 올랐습니다. 대신 useful 자료의 67%가 SUPPORT 미만으로 떨어졌습니다.

  baseline은 필터를 거치기 전, blind judge가 라벨을 붙인 전체 풀의 비율입니다.

  | Mode | useful: baseline → 통과분 (precision) | junk: baseline → 통과분 (filter problem) | useful 중 통과 (recall) | 판단 |
  |---|---|---|---|---|
  | `academic` | 28% → 68% | 13% → 0% | 63% | 권장 |
  | `news` | 21% → 52% | 16% → 5% | 42% | 작동하지만 academic보다 약함 |
  | `official-docs` | 11% → 33% | 29% → 13% | 50% | 작동하지만 academic보다 약함 |
  | `community-opinion` | 19% → 40% | 33% → 30% | 9% | 개선 필요 |
  | `non-academic` | 22% → 0% | 23% → 100% (3개) | 0% | 개선 필요 |

- **plugin 켬 vs 끔**: plugin을 끄면 검색 요약 텍스트를 통해 저품질 자료 24건이 main context에 들어왔고, 켜면 0건이었습니다. 원문을 읽은 페이지는 23개에서 150개로 늘었지만, 비용도 $11.42에서 $15.53으로 36% 늘었습니다(20문항 합, list price).
- **한계**: 질문당 judge 1명, 실행 1회라 benchmark가 아니라 case study입니다. pilot 당시 Step 2.5 judge는 plugin 코드가 직접 페이지를 받는 방식이었는데, eval 환경이 그 요청을 막아 한 번도 판정하지 못했습니다. 지금의 judge는 WebFetch로 읽는 subagent입니다. 이후의 도메인 보강, official-docs 개선, 현재 skill 구성은 아직 재측정하지 않았습니다.
