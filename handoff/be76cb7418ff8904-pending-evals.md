# Handoff: 대기 중인 평가 모음

변경은 반영했지만 아직 평가하지 않은 항목을 한곳에 모은다. 비싼 실행(arms, blind judge)은 한 번에 묶어 돌린다.
항목을 끝내면 결과를 `policy-eval/METHODOLOGY.md` Run log에 남기고 여기서 지운다. 관련 이슈에도 결과를 남긴다.

## 실행 묶음
| 묶음 | 도구 | 포함 항목 |
|---|---|---|
| arms 재실행 | `claude plugin eval` + `policy-eval/arms.py` | E1, E3, E4, E5 |
| held-out blind judge | Workflow blind judge (METHODOLOGY Runs 1) + `contrast.py` | E6(선행), E2, E3 |

## 항목

### E1. official-docs: 공식 페이지가 읽기 대상에 들어갔나 (issue #5 완료 조건 2)
- 변경: Step 1 `site:<공식 host>` 질의, Step 2 `--official-domain`(mod: `officialDomains`). 미등록 host만 tier 2와 `official-claimed`를 받는다.
- 측정: od 문항 with-arm에서 공식 페이지가 PRIMARY 또는 USE 판정 SUPPORT로 읽힌 주제 수. 기준은 `results/pilot.summary.json`. od9(Biome, 미등록 공식 docs)를 추가한다.
- 기존 `runs/*.urls.json`에는 `site:` 결과가 없어 재채점으로는 잴 수 없다.

### E2. na/co 도메인 등록의 out-of-sample 효과 (issue #5 완료 조건 1)
- 변경(2026-10-25): non-academic은 `severalnines.com`을 신규 등록(2)했다. 기존 tier 4 블로그는 대규모 회사와 개인을 2로, 벤더와 마케팅 혼재 블로그를 3으로 옮겼다. community-opinion은 `addyo.substack.com`, `rubyevents.org`를 2로 등록했다.
- in-sample(2026-10-24 라벨): na recall loss 0.977 → 0.930, co 0.900 → 0.780, precision loss 변화 없음. 후보를 이 라벨에서 골랐으므로 검증이 아니다.
- 측정: 아직 judge하지 않은 문항(na4·5·7-10, co4·5·7-10)에 새 라운드를 돌려 recall과 precision을 잰다. tier 2로 올린 블로그가 marginal이나 junk를 통과시키는지 본다. 재배치한 37개는 pilot 풀에 거의 없어 아직 근거가 없다.
- 알려진 한계: useful인데 떨어지는 URL은 도메인당 1건인 long tail이다. 도메인 등록으로는 recall이 크게 줄지 않는다. 후속 설계 후보는 na/co에서 미등록 tier 5 일부를 Step 2.5 judge로 내용 판정하는 방안이다(별도 이슈).

### E3. tier 2 승격의 judge 안전장치 (issue #5)
- 근거: tier 2(SUPPORT)는 Step 2.5 judge가 페이지마다 거른다.
- 측정: `official-claimed`와 신규·재배치 tier 2 도메인의 `judge_support` USE/SKIP 비율. blind label 대비 SKIP이 junk를 잡는지도 본다.

### E4. academic 원 출처 추적 (issue #5)
- 변경: academic에서 Step 5 Verify를 항상 수행한다. 2차 수치는 원 출처를 Steps 2-2.5로 다시 거친다. 재검색과 별개로 1회(`trace`)다.
- 측정: trace 라운드 발생률, 원 출처를 찾아 읽은 비율, 추가 비용(arms cost breakdown).

### E5. 메인 context 오버헤드와 fetch 실패 (issue #4 완료 조건)
- 변경: `score_sources`와 `srcscore.py --format compact`는 PRIMARY/SUPPORT 행과 verdict별 개수만 돌려준다. 전체 표는 side view와 `.tmp/score-*.txt`에 남는다. searcher 반환은 mod가 `.tmp/batch-<id>.json`에 저장하고 메인에는 batch id만 넘긴다(skill-only는 subagent가 파일을 쓰고 경로만 반환). 표의 URL은 fetch 주소다(PMC 구 주소 → `pmc.ncbi.nlm.nih.gov`, 2312 이후 arXiv abs/pdf → html). Step 3은 WebFetch 잘림 note가 있으면 필요할 때만 `offset`으로 이어 읽게 한다.
- 기준: `results/pilot.summary.json`(with $0.78/문항, 메인 $0.60, 16.9턴, 메인이 읽은 페이지 7.5, 메인 오버헤드 약 20k자: 표 8.9k + searcher 10.9k).
- 측정: arms 20문항의 문항당 비용, 턴 수, 메인 peak context, `searcher_contract.chars_in_main`(`arms.py`). fetch 실패 수(`fetch_failures`)와 offset 이어읽기 횟수도 본다. E1, E4와 같은 실행에서 잰다.
- 주의: batch 파일은 `.tmp`에 있으므로 arms 실행 직후, `.tmp`를 지우기 전에 `arms.py ingest`를 돌린다.

### E6. blind judge의 snippet 라벨을 개별 페이지 판정으로 교체 (방법론 변경, 미반영)
- 문제: blind judge(Runs 1)와 union labeller(Runs 4)는 fetch하지 않은 URL에 `basis: snippet` 라벨을 매긴다. WebSearch는 결과마다 snippet을 주지 않고 묶음 요약 하나를 돌려준다. 그래서 이 라벨은 제목과 묶음 요약만 보고 매긴 것이고, URL 하나하나를 평가한 게 아니다.
- 현황(2026-10-24, 1148 URL): snippet 457건(40%). 이 중 fetch 실패가 131건, 미시도가 약 326건이다. 라벨 분포는 fetched가 useful 28%·junk 15%, snippet이 useful 9%·junk 35%다. A의 recall/precision loss와 arms contamination이 이 라벨에 기대고 있다.
- 제안: 수집과 판정을 나눈다. 수집은 지금처럼 Opus WebSearch로 하고 라벨을 매기지 않는다. 판정은 URL마다 독립 labeller가 본문을 읽고 한다. 본문을 못 얻은 URL은 `unreachable`로 표시하고 측정에서 뺀다.
- 주의: WebFetch에는 이미 Haiku 요약기가 들어 있다. Haiku agent가 WebFetch로 판정하면 Haiku 요약을 보고 Haiku가 판정하게 된다. plugin arm도 WebFetch로 읽으므로 judge와 오류가 서로 상관될 수 있다. `fidelity/pagetext.py`(curl, 모델 없음)는 PDF를 추출하지 못한다.
- 미결정: (1) 본문 출처: pagetext raw, WebFetch, raw를 먼저 쓰고 실패하면 WebFetch 중 무엇 (2) labeller 모델: Haiku, Sonnet, Opus, Haiku + Opus 표본 일치도 확인 중 무엇 (3) 다시 판정할 범위: snippet 457건만, 또는 1148건 전부(전부면 regression 기준선의 날짜가 바뀐다) (4) Runs 4에도 적용할지
- 완료 조건: METHODOLOGY Runs 1·4를 갱신한다. 정한 범위로 다시 판정하고 Run log에 기록한다. `contrast.py`와 `arms.py analyze`를 다시 실행해 이전 수치와 비교한다. E2·E3 측정 전에 반영한다.
