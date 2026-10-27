# Handoff: skill 후속 작업과 배포

2026-10-26 arms 실행(21문항, main Haiku), 2026-10-27 arms 실행(main Sonnet), probe에서 드러났지만 아직 손대지 않은 skill 동작 문제와 배포에 남은 일이다. 평가 항목은 `handoff/be76cb7418ff8904-pending-evals.md`에 있다. 여기 항목을 고치면 그쪽 다음 실행 묶음에서 함께 잰다.

## skill 동작

### judge가 SUPPORT 일부를 빠뜨림
- 현상: nw6에서 judge subagent가 SUPPORT 20건 중 2건에 답하지 않았다. 2026-10-27에는 nw2에서 14건 중 5건이 판정 없이 남았다. main은 그중 1건을 판정 없이 읽었다(`main_reads_by_in_run_verdict`의 `SUPPORT`). 전체로는 SUPPORT 165건 중 7건이 판정 없이 남았다(`support_without_verdict`).
- SKILL.md Step 2.5는 판정이 없는 URL을 어떻게 할지 말하지 않는다. "답에 없는 URL은 judge에게 한 번 다시 보낸다"와 "UNJUDGED처럼 Step 3에서 뺀다" 중 정한다.

### main이 Step 2.5 judge를 시도하지 않고 건너뜀
- 현상(2026-10-27, Sonnet main): ac2, co3, od2에서 main이 judge subagent를 띄우지 않았다. 그런데도 `Degraded:`에는 `judge-unavailable`이라고 적었다. 판정 없이 남은 SUPPORT는 ac2 3건, co3 1건, od2 8건이고, od2는 그중 2건을 읽었다. `judge-unavailable`은 subagent를 쓸 수 없을 때를 위한 값인데, 단계를 생략할 때 쓰였다.
- SKILL.md Step 2.5에 "subagent를 띄울 수 있으면 반드시 띄운다"를 분명히 적는다. `judge-unavailable`은 시도가 실패했을 때만 쓰게 한다. 생략하면 그 사실이 따로 드러나게 할지도 정한다.

### academic Step 5 Verify가 돌지 않음
- 현상(2026-10-27, Sonnet main): academic 4문항 모두 Verify subagent를 띄우지 않았다. ac3와 ac6은 main이 inline으로 확인했다고 적었고, ac1과 ac2는 건너뛰었다. ac2는 `Degraded:`에 "Step 5 not run"이라고 적었다. 그래서 2차 수치의 원 출처 추적(`trace` 라운드)이 한 번도 없었고, pending-evals E4는 측정할 것이 없었다.
- SKILL.md Step 5가 academic에서 기본이라는 점과 subagent로 한다는 점이 지켜지지 않는다. inline 대체를 허용할지, `Degraded:`에 올릴 항목(예: `verify-skipped`)을 둘지 정한다.

### 같은 논문이 여러 행을 차지함
- 현상: arXiv `abs`·`pdf`·`html`·버전 접미사(`v1`), 출판사 주소, S2 페이지가 같은 논문으로 따로 채점된다. ac1에서는 2307.13702가 SUPPORT 4행이었다. 표의 자리와 judge 호출이 낭비된다. na6에서 표에는 `pdf`가 있는데 main이 `abs`를 읽어 표 밖 읽기로 잡혔다.
- 후보: `srcscore.py`가 식별자(arXiv id, DOI)가 같은 행을 하나로 묶고 가장 읽기 좋은 주소를 남긴다. `arms.norm`도 같은 기준으로 묶어야 label과 이어진다.

### 조회 실패 원인 미확인
- 현상: 2026-10-26 arms with-arm의 `lookups-failed`가 합계 39건(문항별 최대 12/69)이다. S2 API의 429(키 없이 부르면 자주 남)인지, eval proxy의 응답 끊김이 재시도 뒤에도 남은 것인지 모른다. `srcscore.py` stderr는 host별 실패 수를 말하지 않는다.
- 할 일: 실패를 host와 사유별로 세서 stderr에 남기거나, `--format json` 행에 실패 host를 적는다. S2라면 `SRCSCORE_S2_KEY` 같은 키 지원이나 OpenAlex 우선을 검토한다.

## 배포
- **PR:** `setup/eval` → master.
- **marketplace:** `Doggy-Footprint/claude-plugins`의 `scored-web-search` 항목을 0.3.0으로 올린다(이 repo 밖, 확인 안 함). 그 전까지 `claude plugin install`로 받은 설치는 mod가 있는 0.2.0이다.
- **README 평가 절:** 2026-10-27 arms 결과를 더했다. policy 표는 아직 2026-10-24 Opus 라벨 기준이다. 새 라벨이 나오면 다시 쓴다.
- **issue #4·#5:** 2026-10-27 arms 결과를 남겼다. E3의 B와 E2는 라벨이 나온 뒤 남긴다.
