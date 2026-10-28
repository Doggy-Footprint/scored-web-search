# Handoff: skill 후속 작업과 배포

skill 동작과 배포에서 아직 정하거나 처리할 일이다. 평가 항목은 `handoff/be76cb7418ff8904-pending-evals.md`에 있다.

## skill 동작
2026-10-26·27 arms에서 드러난 judge 누락·생략, academic Verify 미실행, 같은 논문 중복, 조회 실패 원인 미확인은 PR #13에서 고쳤고 효과는 pending-evals E9에서 잰다.
- **S2 키 지원:** E9에서 조회 실패 대부분이 `api.semanticscholar.org http-429`로 나오면 `SRCSCORE_S2_KEY` 같은 키 지원을 정한다. arXiv는 OpenAlex를 먼저 부르고 S2는 fallback이다(`fetchers.arxiv_lookup`).
- **예전 PubMed 주소:** `www.ncbi.nlm.nih.gov/pubmed/<n>`에서 PMID를 못 뽑는다(issue #12).

## 배포
- **marketplace:** `Doggy-Footprint/claude-plugins`의 `scored-web-search` 항목을 0.3.0으로 올린다(이 repo 밖, 확인 안 함). 그 전까지 `claude plugin install`로 받은 설치는 mod가 있는 0.2.0이다.
- **README 평가 절:** 2026-10-27 arms 결과를 더했다. policy 표는 아직 2026-10-24 Opus 라벨 기준이다. 새 라벨이 나오면 다시 쓴다.
- **issue #4·#5:** 2026-10-27 arms 결과를 남겼다. E3의 B와 E2는 라벨이 나온 뒤 남긴다.
