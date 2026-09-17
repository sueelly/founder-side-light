# 폐지한 카카오 실행 경로의 테스트 보관

2026-09-18 터미널 전환 직전 테스트 원본을 보관한 폴더입니다. 현재 pytest의 실행 대상은 루트 `tests/`입니다.
실패를 숨기는 대신 제거한 제품 기능과 유지할 규칙을 다음과 같이 구분했습니다.

| 이전 검사 | 현재 검사 |
| --- | --- |
| test_computer_use.py, test_desktop.py | 카카오 화면·관찰·전송 기능 폐지. test_terminal_ui.py, test_terminal_contract.py에서 로컬 입력·출력·중복 방지 검증 |
| test_allocation.py | 같은 확정 후보군·선행 후보·무작위 배정·재시작 고정을 새 test_allocation.py에서 검증 |
| test_interview.py | 시간·종료·재개·평가 대기 규칙을 test_interview.py, test_interview_edges.py, test_terminal_contract.py에서 검증 |
| test_finalization.py | HTTP 제출·접수 폐지. 실제 인용·최종 기준·완료 재사용을 test_finalization.py와 계약 검사로 이전 |
| test_provider.py | 도구 없는 역할별 입력·인증 환경·취소 가능한 실제 가짜 프로세스를 새 provider/auth 검사로 이전 |
| test_startup.py, test_setup.py | 운영 로그인·방 설정 폐지. 의존성·본인 Claude 로그인·사용자 확인·배포를 새 startup/setup/packaging 검사로 이전 |

변경 전 전체 결과는 98개 통과, 1개 실패였습니다. Windows 시작 파일의 한글 정규화 문제는
배포 스크립트에서 수정하고 `test_packaging.py`에 NFD 파일명 회귀 검사를 남겼습니다.
