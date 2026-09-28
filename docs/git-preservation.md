# human-v2 브랜치 보존

2026-09-28에 독립 폴더 복사본을 같은 저장소의 Git worktree로 연결했다.
브랜치는 `experiment/candidate-human-v2-20260918`이며, 기존 실행 코드와 일치하는
`6f905a3`에서 분기했다. 현재 `main`의 설치·폴더 구조 개편은 합치지 않았다.

후보 프롬프트, 두 검증 스크립트, 검사 보고서, 격리 고지와 보고 스크립트,
실험 안내를 목적별 커밋으로 보존했다. 모델을 호출하거나 기준·평가·순위를 새로 작성하지 않았다.

`ISOLATION-NOTICE.json`과 `docs/isolation-and-handoff.md`는 2026-09-18 당시의 기록이다.
여기에 있는 이전 절대 경로와 Git 미연결 설명은 당시 상태를 나타낸다.
`scripts/report_human_v2_isolation.py`도 그때의 경로와 원본 구조를 가정하는 기록용 스크립트이며,
현재 워크트리 상태를 확인하는 도구가 아니다. JSON 고지와 기존 검사 결과는 수정하지 않았다.

`.runtime/`, `.venv/`, 로그와 인증 파일은 Git에서 제외한다. 실제 모델 검사 원문은
로컬 `.runtime/verification/`에 보존되어 있으며, 이번 브랜치 저장에 포함하지 않았다.

## 저장 시점 검사

- `.venv/bin/python -m pytest -q`: 149개 통과, 1개 실패.
- 실패: `tests/test_provider.py::test_timeout_terminates_real_subprocess`의
  `fake CLI did not start`. 테스트는 로컬 가짜 CLI에 0.3초 제한을 적용한다.
- 변경 없는 `main`과 리허설 복사본에서도 같은 검사가 실패했다. `main`의 해당 검사만
  따로 실행했을 때도 재현했다. 이번 저장 작업에서 원인을 확정하거나 코드를 수정하지 않았다.
- `.venv/bin/python -m harness doctor`: 통과.
- 실제 Claude 호출, 사용자 면접, Windows 실기 검사는 이번에 실행하지 않았다.

과거 150개 통과 기록은 그 당시 결과이며, 위 결과는 2026-09-28 현재 환경에서의 재검사다.
