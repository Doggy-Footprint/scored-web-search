# Handoff: 대기 중인 평가 모음

변경은 반영했지만 아직 평가하지 않은 항목을 한곳에 모은다. 아직 고치지 않은 skill 동작 문제는 `handoff/599a859dd32c2d8b-skill-followups.md`에 있다. 비싼 실행(arms, blind judge, union labeller)은 한 번에 묶어 돌린다.
항목을 끝내면 결과를 `policy-eval/METHODOLOGY.md` Run log에 남기고 여기서 지운다. 관련 이슈에도 결과를 남긴다.

## 실행 환경 (확인됨)
eval은 skill이 의도대로 동작할 때의 효과를 잰다. repo 루트 plugin은 skill만 담는다. eval 안에서 각 단계가 할 수 있는 일은 2026-10-26 probe(`policy-eval/probe/`, METHODOLOGY "Environment probes")와 arms 1회로 확인했다.
- **네트워크:** eval의 Bash는 `WebFetch(domain:…)`으로 이름을 준 host에만 닿는다. arms는 scorer host(`fetchers.LOOKUP_HOSTS`) 다섯을 항상 허용한다(명령은 METHODOLOGY Runs 2). eval proxy는 응답을 가끔 중간에 끊고, scorer는 끊긴 응답을 한 번 다시 받는다.
- **파일:** Bash는 skill 폴더만 읽는다(`policy-eval/`은 막힘). `/tmp`는 막히고 `$TMPDIR`과 작업 폴더는 된다.
- **측정:** `arms.py`는 skill 각 단계의 출력(점수표, URL 파일, subagent 답)과 보고서 끝의 `Degraded:` 줄을 파싱하고, 그 줄을 trace에서 센 값과 대조한다. judge의 UNJUDGED는 `page:`와 `fetch-error:`로 나눠 센다.
- **WebFetch rate limit:** 2026-10-26 arms(`-j 2`)에서 judge의 `fetch-error`는 2건이었다. `-j 2`면 충분하다.
- **기준선:** pilot(2026-10-24)은 mod 구성이었고 Step 2.5 judge가 한 번도 판정하지 못했다. 2026-10-26 arms는 main이 Haiku였다. 둘 다 다음 실행과 직접 비교하지 않고 참고만 한다. pilot 산출물은 `runs/pilot-2026-10-24/`, 2026-10-26 산출물은 `runs/`에 있다(둘 다 tracked 아님).

## 다음 실행 묶음
아래를 한 번에 돌린다. 순서가 있다.
1. **arms 재실행** (`claude plugin eval` + `arms.py ingest/analyze`): E3, E4, E5, E7, 그리고 E8은 grant 없이 한 case만. main 모델을 정한다. Opus면 실사용 조건이고, Haiku면 2026-10-26과 비교할 수 있다.
2. **union labeller** (Workflow, METHODOLOGY Runs 4): 1의 `runs/<id>.union.todo.json`을 라벨한다. todo는 main context에 들어간 URL(`listed`/`exposed`/`read`)만 담는다. subagent만 본 URL은 측정에 쓰이지 않는다. 2026-10-26 실행 기준 914건이다. 라벨은 (질문, URL) 단위로 쌓여, 같은 질문을 다시 돌리면 새 URL만 붙이면 된다. labeller 모델과 본문 출처는 E6에서 정한다.
3. **held-out blind judge** (Workflow, METHODOLOGY Runs 1) + `contrast.py`: E6(선행), E2.

## 항목

### E2. na/co 도메인 등록의 out-of-sample 효과 (issue #5 완료 조건 1)
- 변경(2026-10-25): non-academic은 `severalnines.com`을 신규 등록(2)했다. 기존 tier 4 블로그는 대규모 회사와 개인을 2로, 벤더와 마케팅 혼재 블로그를 3으로 옮겼다. community-opinion은 `addyo.substack.com`, `rubyevents.org`를 2로 등록했다.
- in-sample(2026-10-24 라벨): na recall loss 0.977 → 0.930, co 0.900 → 0.780, precision loss 변화 없음. 후보를 이 라벨에서 골랐으므로 검증이 아니다.
- 측정: 아직 judge하지 않은 문항(na4·5·7-10, co4·5·7-10)에 새 라운드를 돌려 recall과 precision을 잰다. tier 2로 올린 블로그가 marginal이나 junk를 통과시키는지 본다. 재배치한 37개는 pilot 풀에 거의 없어 아직 근거가 없다.
- 알려진 한계: useful인데 떨어지는 URL은 도메인당 1건인 long tail이다. 도메인 등록으로는 recall이 크게 줄지 않는다. 후속 설계 후보는 na/co에서 미등록 tier 5 일부를 Step 2.5 judge로 내용 판정하는 방안이다(별도 이슈).

### E3. tier 2 승격의 judge 안전장치 (issue #5)
- 근거: tier 2(SUPPORT)는 Step 2.5 judge가 페이지마다 거른다.
- 2026-10-26(Haiku main): judge가 SUPPORT 158건을 판정했다(USE 58, SKIP 58, `page:` 40, `fetch-error:` 2). 판정 없이 남은 SUPPORT는 7건이다. blind label이 없어 판정이 맞았는지는 아직 모른다.
- 측정: `official-claimed`와 신규·재배치 tier 2 도메인에서 judge의 USE/SKIP 비율. union label 대비 SKIP이 junk를 잡는지(`judge_vs_blind`)를 본다.

### E4. academic 원 출처 추적 (issue #5)
- 변경: academic에서 Step 5 Verify를 항상 수행한다. 2차 수치는 원 출처를 Steps 2-2.5로 다시 거친다. 재검색과 별개로 1회(`trace`)다.
- 측정: trace 라운드 발생률, 원 출처를 찾아 읽은 비율, 추가 비용. `arms.py`는 아직 round 종류(`trace`)를 구분하지 않는다. 보고서의 `Keywords:` 줄에서 읽는 방안을 먼저 정한다.

### E5. 메인 context 오버헤드와 fetch 실패 (issue #4 완료 조건)
- 변경: `srcscore.py --format compact`는 PRIMARY/SUPPORT 행(40자로 자른 제목 포함)과 verdict별 개수만 돌려주고, 전체 표는 `--full-out` 파일에 남긴다. Step 1 subagent는 URL 파일을 쓰고 경로와 개수만 돌려준다. 표의 URL은 fetch 주소다(doi.org → 대상 주소, PMC 구 주소 → `pmc.ncbi.nlm.nih.gov`, 2312 이후 arXiv abs/pdf → html). Step 3은 WebFetch 잘림 note가 있으면 필요할 때만 `offset`으로 이어 읽게 한다.
- 측정: 문항당 비용, 턴 수, 메인 peak context, `main_context_chars`(표와 subagent 반환), `peeks`, `fetch_failures`, offset 이어읽기 횟수.
- 2026-10-26(Haiku main, 21문항): 표가 main context에 넣은 글자는 합계 89k, 문항당 약 4.3k다. main이 11문항에서 URL 파일이나 전체 표를 직접 열어 100k를 더 넣었다(E7). peak context 중앙값은 with 44.5k, without 24.6k다.

### E7. 엿보기 수정 확인
- 문제(2026-10-26): main이 21문항 중 11문항에서 URL 파일이나 `--full-out` 표를 `cat`·python·Read로 열었다. 명령 설명은 "List titles", "Show full scored table with titles"였다. compact 표에 제목이 없어 고르려고 연 것으로 본다.
- 변경: compact 행에 제목을 40자로 잘라 붙였고, SKILL.md Step 2에 그 파일들을 열지 말라고 적었다.
- 측정: `peeks`가 있는 문항 수와 글자 수. 남으면 plugin command hook(PreToolUse로 main의 `$TMPDIR/ssw/` 읽기를 막음)을 단다. hook은 Claude Code plugin 설치에서만 동작하고, eval이 skill과 hook을 함께 재게 된다는 점을 감안해 정한다.

### E8. scorer host가 막힌 환경에서 skill 전체가 멈추나
- 확인한 것: probe P1은 `srcscore.py` 단독으로, grant가 없으면 exit 4와 허용할 host 안내가 나온다는 것만 봤다.
- 측정: arms case 하나(예: ac1)를 host grant 없이 돌린다. main이 점수 없이 읽지 않고 멈추는지, 사용자에게 host 다섯을 안내하는지, `--no-net`으로 다시 돌리지 않는지 본다.

### E6. blind judge의 snippet 라벨을 개별 페이지 판정으로 교체 (방법론 변경, 미반영)
- 문제: blind judge(Runs 1)와 union labeller(Runs 4)는 fetch하지 않은 URL에 `basis: snippet` 라벨을 매긴다. WebSearch는 결과마다 snippet을 주지 않고 묶음 요약 하나를 돌려준다. 그래서 이 라벨은 제목과 묶음 요약만 보고 매긴 것이고, URL 하나하나를 평가한 게 아니다.
- 현황(2026-10-24, 1148 URL): snippet 457건(40%). 이 중 fetch 실패가 131건, 미시도가 약 326건이다. 라벨 분포는 fetched가 useful 28%·junk 15%, snippet이 useful 9%·junk 35%다. A의 recall/precision loss와 arms contamination이 이 라벨에 기대고 있다.
- 제안: 수집과 판정을 나눈다. 수집은 지금처럼 Opus WebSearch로 하고 라벨을 매기지 않는다. 판정은 URL마다 독립 labeller가 본문을 읽고 한다. 본문을 못 얻은 URL은 `unreachable`로 표시하고 측정에서 뺀다.
- 주의: WebFetch에는 이미 Haiku 요약기가 들어 있다. Haiku agent가 WebFetch로 판정하면 Haiku 요약을 보고 Haiku가 판정하게 된다. skill의 Step 2.5 judge와 with-arm도 WebFetch로 읽으므로, labeller와 오류가 서로 상관될 수 있다. `fidelity/pagetext.py`(curl, 모델 없음)는 PDF를 추출하지 못한다.
- 미결정: (1) 본문 출처: pagetext raw, WebFetch, raw를 먼저 쓰고 실패하면 WebFetch 중 무엇 (2) labeller 모델: Haiku, Sonnet, Opus, Haiku + Opus 표본 일치도 확인 중 무엇 (3) 다시 판정할 범위: snippet 457건만, 또는 1148건 전부(전부면 regression 기준선의 날짜가 바뀐다) (4) Runs 4(union labeller)에도 적용할지. union labeller는 다음 실행 묶음 2에서 바로 쓰이므로 (4)를 그 전에 정한다.
- 완료 조건: METHODOLOGY Runs 1·4를 갱신한다. 정한 범위로 다시 판정하고 Run log에 기록한다. `contrast.py`와 `arms.py analyze`를 이전 라벨과 새 라벨로 각각, 같은 코드로 돌려 비교한다. `arms.py`의 산출 항목은 2026-10-26에 바뀌었으므로(`listed`에 표 행 포함, 비용 역할은 `main`/`subagent`, skill 단계 측정 추가) tracked `results/pilot.summary.json`과 직접 비교하지 않는다.
