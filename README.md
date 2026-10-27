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
- **커스터마이징**: `policy.json`과 `modes/*.json`을 고친 뒤 `python3 scripts/check_policy.py`로 검증합니다. 한 mode의 질문에만 의미가 있는 도메인은 그 mode의 overlay(`modes/*.json`)에 등록합니다.

# 평가

자세한 방법론은 [`policy-eval/METHODOLOGY.md`](policy-eval/METHODOLOGY.md)에 있습니다. pilot은 mode당 4문항, 20문항입니다. 2026-10-24에 main Opus로 처음 돌렸고, 2026-10-27에 od9를 더한 21문항을 현재 skill 구성과 main Sonnet으로 다시 돌렸습니다.

- **방법**: policy를 모르는 blind judge가 직접 검색한 URL에 `useful` / `marginal` / `junk` 라벨을 붙이고, 이를 policy의 verdict와 비교합니다. plugin을 켠 실행과 끈 실행도 같은 질문으로 비교합니다. 보고서 자체의 정확도는 측정하지 않았습니다.
- **Policy (2026-10-24, Opus judge 라벨)**: 통과분의 useful 비율이 전체 풀의 20%에서 51%로 올랐습니다. 대신 useful 자료의 67%가 SUPPORT 미만으로 떨어졌습니다. 이 라벨은 2026-10-27에 버렸습니다. 이후의 도메인 보강과 Sonnet 라벨 기준 재측정은 아직 하지 않았습니다.

  baseline은 필터를 거치기 전, blind judge가 라벨을 붙인 전체 풀의 비율입니다.

  | Mode | useful: baseline → 통과분 (precision) | junk: baseline → 통과분 (filter problem) | useful 중 통과 (recall) | 판단 |
  |---|---|---|---|---|
  | `academic` | 28% → 68% | 13% → 0% | 63% | 권장 |
  | `news` | 21% → 52% | 16% → 5% | 42% | 작동하지만 academic보다 약함 |
  | `official-docs` | 11% → 33% | 29% → 13% | 50% | 작동하지만 academic보다 약함 |
  | `community-opinion` | 19% → 40% | 33% → 30% | 9% | 개선 필요 |
  | `non-academic` | 22% → 0% | 23% → 100% (3개) | 0% | 개선 필요 |

- **plugin 켬 vs 끔 (2026-10-27, main Sonnet, 21문항)**: 라벨이 아직 없어 저품질 자료 유입은 재지 못했습니다. 아래는 문항당 평균입니다.

  | | plugin 켬 | plugin 끔 |
  |---|---|---|
  | 비용 (list price) | $0.31 | $0.20 |
  | 턴 수 | 7.9 | 4.7 |
  | main이 원문을 읽은 페이지 | 4.6 | 0.4 |
  | main peak context (토큰) | 35.5k | 25.4k |

  - plugin을 켜면 skill이 21문항 모두에서 실행됐습니다. main이 읽은 97페이지 중 94페이지가 PRIMARY 또는 judge가 USE로 판정한 SUPPORT였습니다.
  - judge는 SUPPORT를 USE 38, SKIP 47, 판정 불가 21로 갈랐습니다.
  - 점수 표와 subagent 답이 main context에 넣은 글자는 문항당 3.5k자입니다. 2026-10-24에는 약 20k자였습니다.
  - scorer가 외부 조회 host에 닿지 못하면 skill은 점수 없이 읽지 않고 멈춥니다. 허용할 host를 안내하는 것도 확인했습니다.
  - 지키지 않은 단계도 있습니다. 3문항에서 Step 2.5 judge를 건너뛰었고, academic 4문항 모두 Step 5 Verify를 subagent로 돌리지 않았습니다.
- **한계**: 질문당 judge 1명, 실행 1회라 benchmark가 아니라 case study입니다. 2026-10-24와 2026-10-27은 main 모델과 skill 구성이 달라 직접 비교할 수 없습니다. 2026-10-24에는 Step 2.5 judge가 plugin 코드로 페이지를 받는 방식이었고, eval 환경이 그 요청을 막아 한 번도 판정하지 못했습니다.
