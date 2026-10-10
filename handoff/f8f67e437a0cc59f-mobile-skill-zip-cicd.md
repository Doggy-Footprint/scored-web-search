# Handoff: 모바일용 skill zip CI/CD

## 목표
모바일(claude.ai 채팅, 클라우드 세션)에서 scored-web-search를 PC 없이 쓴다.
master push 시 GitHub Action이 `skills/scored-web-search/`를 zip으로 만들어 배포 → 사용자가 claude.ai에 업로드.

## 완료된 것
- `feat/mod` → master merge (PR #1). mod + self-contained skill 폴더.
- 마켓플레이스 저장소 `Doggy-Footprint/claude-plugins` (public, 로컬 `~/tools/claude-plugins`, branch `main`).
  - marketplace `doggy-footprint`, entry `scored-web-search` → github `Doggy-Footprint/scored-web-search` ref `master`.
- README에 마켓플레이스 설치 줄 추가.
- 검증: 마켓플레이스로 설치 후 플래그 없는 `claude`에서 `score_sources`, `judge_support`, `scored-web-search:searcher` 로드 확인 (사용자 직접).
  plugin은 세션 시작 시 로드되므로 설치 전 시작한 세션에선 안 보임.

## 문서로 확인한 사실 (code.claude.com, 2026-10-10)
- 클라우드 세션은 plugin을 설치하지 않음 (repo `.claude/settings.json`의 `enabledPlugins`/`extraKnownMarketplaces` 포함, user scope도 불가) → mod는 클라우드/모바일 단독 사용 불가.
- claude.ai 계정에서 켠 skill은 클라우드 세션 시작 시 자동 로드. repo `.claude/skills/`도 로드.
- claude.ai skill은 업로드 방식. GitHub/마켓플레이스 자동 동기화, 업데이트 API/CLI는 문서에 없음 → 업데이트마다 재업로드.
- 업로드 frontmatter 허용 키: name, description, license, compatibility, metadata, allowed-tools. 현재 SKILL.md는 name/description만 → OK.
- 클라우드 VM: Ubuntu 24.04, Python 3.x 있음.
- 네트워크 기본 Trusted. scorer가 호출하는 `api.openalex.org`, `api.semanticscholar.org`, `doi.org`, `hn.algolia.com`은 기본 허용 목록에 없을 가능성 높음 (`api.github.com`은 GitHub proxy 경유 가능성). 환경을 Custom(도메인 추가) 또는 Full로 바꿔야 함.

## 미확인
- claude.ai 채팅 샌드박스(코드 실행)의 네트워크 정책 — scorer 외부 조회 가능 여부.
- 재업로드 시 기존 skill 교체인지 별도 생성인지.
- Skills API로 claude.ai 계정 skill을 갱신할 수 있는지.
- Remote Control 상시 대기 모드(`claude remote-control`) 동작.
- 모바일에서 사이드 뷰 렌더링.

## 결정 필요 (CI/CD)
1. 배포 형태: GitHub Release asset / Actions artifact / 고정 URL(예: `latest` release).
2. 트리거: master push 전부 vs `skills/scored-web-search/**` 변경 시만. 버전/태그 규칙.
3. zip 내용: 폴더 그대로? `__pycache__` 등 제외 규칙. zip 최상위가 폴더여야 하는지(claude.ai 업로드 형식 확인 필요).
4. 배포 전 게이트: `python3 -m unittest discover tests`, `scripts/check_policy.py` 실행 여부.
5. README에 모바일 사용법(업로드 + 네트워크 Custom 설정) 추가 여부.

## 참고
- 테스트용 임시 마켓플레이스 `swstest`를 실제 `~/.claude`에 등록했었음 → `claude plugin marketplace remove swstest`로 정리됐는지 확인.
