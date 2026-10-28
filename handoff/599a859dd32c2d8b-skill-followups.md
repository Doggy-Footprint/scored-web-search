# Handoff: skill 후속 작업과 배포

2026-10-26 arms 실행(21문항, main Haiku), 2026-10-27 arms 실행(main Sonnet), probe에서 드러난 skill 동작 문제를 어떻게 반영했는지와 배포에 남은 일이다. 평가 항목은 `handoff/be76cb7418ff8904-pending-evals.md`에 있다.

## skill 동작
2026-10-26·27 arms와 probe에서 드러난 다섯 문제는 2026-10-28에 반영했다. 효과는 `handoff/be76cb7418ff8904-pending-evals.md` E9에서 다음 arms 때 잰다.
- **judge 누락:** 답에 없는 SUPPORT URL은 judge에게 한 번 더 보낸다. 그래도 없으면 UNJUDGED `missing:`이다(SKILL.md Step 2.5). `Degraded:`는 `unjudged <u> (fetch-error <f>, page <p>, missing <m>)`다. `arms.degraded_traced`는 judge를 부른 라운드에서 판정이 없는 SUPPORT를 `missing`으로 센다.
- **judge 생략:** SUPPORT가 있고 subagent를 띄울 수 있으면 Step 2.5는 필수다. `judge-unavailable`은 띄운 judge가 판정을 못 냈거나 subagent가 아예 없을 때만 쓴다. 그 밖의 생략은 `judge-skipped`다. arms는 Agent prompt의 rubric 답 형식(`USE/SKIP/UNJUDGED`)으로 judge 호출을 알아보고, 라운드의 SUPPORT URL을 prompt나 판정에 하나도 담지 않은 라운드를 skipped로 센다.
- **academic Verify:** academic에서 Step 3이 읽은 것이 있으면 Step 5 Verify는 필수다. subagent를 쓸 수 있으면 subagent, 없을 때만 inline이다. 2차 수치의 trace도 필수다. 돌리지 않으면 `verify-skipped`다. arms는 prompt의 `supported/secondary/contradicted` 형식으로 Verify 호출을 알아본다. eval에서는 subagent를 늘 쓸 수 있으므로 inline 대체도 skipped로 잡힌다.
- **같은 논문 중복:** `srcscore`가 URL의 식별자(arXiv id, DOI, PMID, arXiv DOI는 arXiv id로)와 같은 URL 반복을 한 행으로 묶는다(`scoring.merge_same_paper`, `identifiers.paper_key`). 점수가 가장 높은 행이 남고, 같으면 arXiv html > abs > pdf 순이다. compact footer 끝에 `merged k`가 붙고, json 행에는 `aliases`와 `merged`가 붙는다. arms는 `fetch_aliases`로 `aliases`를 남은 행에 잇고, ingest todo는 이미 라벨된 논문의 다른 사본을 뺀다. 식별자가 URL에 없는 S2 페이지는 묶지 않는다.
- **조회 실패 원인:** `srcscore`가 기존 경고 줄 아래에 `srcscore: failed lookups: <host> <reason> x<n>, ...`을 남긴다(사유: `http-<code>`, `timeout`, `cut-off`, `unreachable`, `bad-json`). OpenAlex 우선은 이미 되어 있었다(`arxiv_lookup`). `SRCSCORE_S2_KEY` 같은 키 지원은 다음 arms에서 실패가 S2 429로 확인되면 정한다.

## 배포
- **PR:** `setup/eval` → master.
- **marketplace:** `Doggy-Footprint/claude-plugins`의 `scored-web-search` 항목을 0.3.0으로 올린다(이 repo 밖, 확인 안 함). 그 전까지 `claude plugin install`로 받은 설치는 mod가 있는 0.2.0이다.
- **README 평가 절:** 2026-10-27 arms 결과를 더했다. policy 표는 아직 2026-10-24 Opus 라벨 기준이다. 새 라벨이 나오면 다시 쓴다.
- **issue #4·#5:** 2026-10-27 arms 결과를 남겼다. E3의 B와 E2는 라벨이 나온 뒤 남긴다.
