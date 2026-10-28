File: f8f67e437a0cc59f-mobile-skill-zip-cicd.md
Summary: 모바일용 skill zip을 master push 시 GitHub Action으로 배포하는 작업 인계
Related Files: skills/scored-web-search/SKILL.md, README.md, .claude-plugin/plugin.json
Related Symbols: srcscore_core.fetchers
---
File: be76cb7418ff8904-pending-evals.md
Summary: 확인된 eval 실행 환경, 다음에 몰아서 돌릴 실행 묶음(arms, union labeller, blind judge), 아직 평가하지 않은 항목 E2-E9
Related Files: policy-eval/METHODOLOGY.md, policy-eval/probe, policy-eval/contrast.py, policy-eval/arms.py, policy-eval/fidelity/pagetext.py, skills/scored-web-search/SKILL.md, skills/scored-web-search/scripts/modes/non_academic.json, skills/scored-web-search/scripts/modes/community_opinion.json, skills/scored-web-search/scripts/modes/official_docs.json
Related Symbols: srcscore_core.scoring.official_claim, srcscore_core.cli.main, srcscore_core.fetchers.LOOKUP_HOSTS, arms.extract, srcscore_core.policy.validate_policy, srcscore_core.identifiers.fetch_url, srcscore_core.io_format.render_compact
---
File: 599a859dd32c2d8b-skill-followups.md
Summary: 2026-10-26·27 arms에서 드러난 skill 동작 후속(judge 누락·생략, academic Verify 미실행, 같은 논문 중복, 조회 실패 원인)을 2026-10-28에 어떻게 반영했는지와 배포에 남은 일
Related Files: skills/scored-web-search/SKILL.md, skills/scored-web-search/scripts/srcscore_core/fetchers.py, skills/scored-web-search/scripts/srcscore_core/identifiers.py, skills/scored-web-search/scripts/srcscore_core/scoring.py, skills/scored-web-search/scripts/srcscore_core/cli.py, skills/scored-web-search/scripts/srcscore_core/io_format.py, policy-eval/arms.py, README.md, .claude-plugin/plugin.json
Related Symbols: srcscore_core.fetchers.http_json, srcscore_core.fetchers.FAILURES, srcscore_core.identifiers.paper_key, srcscore_core.scoring.merge_same_paper, srcscore_core.cli.main, arms.fetch_aliases, arms.degraded_traced, arms.degraded_check, arms.closing_lines, arms.extract
