# Handoff: skill 후속 작업과 배포

2026-10-26 arms 실행(21문항, main Haiku)과 probe에서 드러났지만 아직 손대지 않은 skill 동작 문제, 그리고 배포에 남은 일이다. 평가 항목은 `handoff/be76cb7418ff8904-pending-evals.md`에 있다. 여기 항목을 고치면 그쪽 다음 실행 묶음에서 함께 잰다.

## skill 동작

### judge가 SUPPORT 일부를 빠뜨림
- 현상: nw6에서 judge subagent가 SUPPORT 20건 중 2건에 답하지 않았다. main은 그중 1건을 판정 없이 읽었다(`main_reads_by_in_run_verdict`의 `SUPPORT`). 전체로는 SUPPORT 165건 중 7건이 판정 없이 남았다(`support_without_verdict`).
- SKILL.md Step 2.5는 판정이 없는 URL을 어떻게 할지 말하지 않는다. "답에 없는 URL은 judge에게 한 번 다시 보낸다"와 "UNJUDGED처럼 Step 3에서 뺀다" 중 정한다.

### 같은 논문이 여러 행을 차지함
- 현상: arXiv `abs`·`pdf`·`html`·버전 접미사(`v1`), 출판사 주소, S2 페이지가 같은 논문으로 따로 채점된다. ac1에서는 2307.13702가 SUPPORT 4행이었다. 표의 자리와 judge 호출이 낭비된다. na6에서 표에는 `pdf`가 있는데 main이 `abs`를 읽어 표 밖 읽기로 잡혔다.
- 후보: `srcscore.py`가 식별자(arXiv id, DOI)가 같은 행을 하나로 묶고 가장 읽기 좋은 주소를 남긴다. `arms.norm`도 같은 기준으로 묶어야 label과 이어진다.

### 조회 실패 원인 미확인
- 현상: 2026-10-26 arms with-arm의 `lookups-failed`가 합계 39건(문항별 최대 12/69)이다. S2 API의 429(키 없이 부르면 자주 남)인지, eval proxy의 응답 끊김이 재시도 뒤에도 남은 것인지 모른다. `srcscore.py` stderr는 host별 실패 수를 말하지 않는다.
- 할 일: 실패를 host와 사유별로 세서 stderr에 남기거나, `--format json` 행에 실패 host를 적는다. S2라면 `SRCSCORE_S2_KEY` 같은 키 지원이나 OpenAlex 우선을 검토한다.

## 배포
- **PR:** `setup/eval` → master.
- **marketplace:** `Doggy-Footprint/claude-plugins`의 `scored-web-search` 항목을 0.3.0으로 올린다(이 repo 밖, 확인 안 함). 그 전까지 `claude plugin install`로 받은 설치는 mod가 있는 0.2.0이다.
- **README 평가 절:** 아직 pilot(2026-10-24, mod 구성, judge 미동작) 수치뿐이다. 다음 eval 묶음 뒤에 다시 쓴다.
- **issue #4:** E5를 마치면 결과를 남긴다. issue #5에는 2026-10-26에 E1 결과를 남겼다.
