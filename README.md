# Founder Side · Light

터미널에서 네 명의 인성 면접을 진행하고 대화·평가·최종 순위를 문서로 저장합니다.
첫 면접관은 사용자, 다음 세 면접관은 Claude입니다. 지원자는 모두 Claude가 맡습니다.
회사·직무는 **코어브릿지웍스 / 경영지원·사업운영 담당자**이며 모두 기술면접을 통과한 후보입니다.

## 시작하기

**Python 3.11 이상**과 **본인 계정으로 로그인한 Claude Code CLI**가 필요합니다.
Claude가 없다면 [공식 설치 안내](https://code.claude.com/docs/en/quickstart)를 따르고,
로그인이 필요하면 터미널에서 `claude auth login`을 실행하세요. 모델 호출에는 인터넷과 계정 사용량이 필요합니다.

이 저장소를 `git clone https://github.com/sueelly/founder-side-light.git`으로 받거나,
GitHub의 **Code → Download ZIP**으로 받아 압축을 푸세요. 받은 폴더에서 터미널을 열고 실행합니다.

**macOS / Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock.txt .
.venv/bin/python -m harness start
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock.txt .
.\.venv\Scripts\python.exe -m harness start
```

가상환경 활성화 스크립트나 실행 정책 변경은 필요 없습니다. 설치는 처음 한 번만 하고,
이후에는 마지막 `start` 명령으로 이어서 진행합니다. 별도 설정 파일이나 설치 마법사는 없습니다.

첫 실행에 `.runtime/terminal-light/insights.md`가 만들어집니다. 면접 기준을 작성하고,
터미널에 표시된 내용을 확인한 뒤 `시작`을 입력하세요. 빈 기준으로는 면접이 시작되지 않습니다.
macOS와 Windows에서는 편집기를 열며, 다른 환경에서는 표시된 경로를 직접 편집하면 됩니다.

```text
기준 확인 → 사람 면접 최대 30분 → 본인 평가·기준 변경 여부 확인
         → AI 면접 최대 20분   → 본인 평가·기준 변경 여부 확인
         → AI 면접 최대 20분   → 본인 평가·기준 변경 여부 확인
         → AI 면접 최대 20분   → 본인 평가·기준 변경 여부 확인
         → 최종 기준으로 네 명 순위 저장
```

## 면접 진행

첫 면접에서는 질문을 입력하고 Enter를 누릅니다. 이후 세 면접에서는 두 Claude 역할이 차례대로 대화합니다.
발화가 완성될 때마다 화면과 기록 문서에 반영됩니다.

```text
면접관: 오시온님 면접 시작하겠습니다
면접관: 먼저 간단한 자기소개를 부탁드립니다.
지원자(오시온): …
면접관 >
```

| 입력 | 동작 |
| --- | --- |
| 일반 텍스트 | 첫 면접에서 직접 질문하거나 후보 질문에 답하기 |
| `/last` | 사람 면접 마무리 요청 |
| `/end` | 마무리 안내에 대한 후보 답을 받은 뒤 종료 |
| `/status` | 현재 상태·남은 시간 확인 |
| `/retry` | 실패한 모델 차례 재시도 |
| Ctrl+C | 중단. 같은 명령으로 재개하며 마감 시각은 유지 |

응답 생성 중 새 질문은 접수하지 않습니다. 모델 대기·오류 시간도 면접 시간에 포함합니다.
매 면접 뒤 `feedback.md`의 **종합 평가·근거·우려·확인할 점**을 직접 작성하세요.
인사이트를 고쳤으면 `수정`, 그대로면 `그대로`를 입력합니다. 다음 면접은 다시 `시작`을 입력해야 열립니다.
기준을 자동으로 추가하거나 사용자 평가를 대신 작성하지 않습니다.

## 기록과 재개

기록은 모두 실행한 폴더의 `.runtime/terminal-light/`에 남고 Git에는 포함되지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `insights.md` | 사용자가 작성·수정하는 현재 면접 기준 |
| `01-P07/` ~ `04-…/transcript.md` | 후보별 면접 원문 |
| 각 후보의 `feedback.md` | 사용자가 작성하는 평가 |
| `feedback.confirmed.md`, `insights.before.md`, `insights.after.md` | 확정 평가와 기준 사본 |
| `ranking.md`, `ranking.json` | 네 번째 평가 이후 최종 순위·근거 |
| `state.json` | 하네스가 관리하는 배정·시간·진행 상태 |

직접 편집할 파일은 `insights.md`와 작성 중인 `feedback.md`입니다. 원문·확정 사본·상태를 수정하면 검증에서 멈춥니다.
중단 후에는 같은 폴더에서 `start`를 다시 실행하세요. 배정과 마감 시각은 바뀌지 않습니다.
자료·프롬프트가 바뀐 버전으로 진행 중 세션을 재개할 수는 없습니다.
별도 실습은 `python -m harness --session .runtime/practice-2 start`처럼 새 경로를 지정합니다.
여기의 `python`은 위에서 만든 가상환경의 Python 경로로 바꿔 실행하세요.

## 폴더 안내

처음 사용하는 사람은 `README.md`만 보면 됩니다. 면접 중 직접 만지는 파일은 세션 폴더의
`insights.md`와 각 후보의 작성 중인 `feedback.md`뿐입니다.

```text
README.md                 시작 방법과 면접 진행 안내
harness/                  면접을 진행·기록하는 실행 엔진
  resources/materials/    회사 공개 자료와 후보 제출 자료
  resources/prompts/      지원자·면접관·순위 역할 지시문
  resources/templates/    insights.md·feedback.md 기본 양식
developer/                개발자용 테스트와 품질 검사 도구
developer/docs/           동작 스펙과 검증 기록
.runtime/                 실행할 때 자동으로 생기는 면접 기록 (Git 제외)
```

`harness/resources/` 안의 자료는 실행에 필요한 패키지 데이터입니다. 내용을 직접 고치면
진행 중 세션의 자료 해시와 맞지 않아 재개가 중단될 수 있습니다.

## 개발과 검증

가상환경의 Python으로 실행합니다. 테스트 도구는 개발할 때만 설치합니다.

```sh
python -m pip install -c requirements.lock.txt -e '.[test]'
python -m pytest -q
python -m harness doctor
```

`doctor`는 로컬 자료·설치 상태를 검사합니다. 실제 Claude 응답이나 대화 품질을 보증하지 않습니다.
GitHub Actions는 macOS·Windows의 합성 테스트와 정적 검사를 실행합니다.

현재는 실습용입니다. 실제 네 명 리허설에서 최종 순위 생성 시간 초과와 마무리 질문 중복이 관찰됐고,
후보가 설정에 없는 사실을 덧붙이는 경우도 남아 있습니다. 최종 순위 생성에 실패하면 기록은 보존되며
같은 `start` 명령으로 재시도할 수 있습니다. [검증 범위와 알려진 한계](developer/docs/verification.md)를 참고하세요.

[동작 스펙](developer/docs/terminal-interview-spec.md) · [개발 계획](developer/docs/terminal-interview-plan.md) ·
[후보 대화 품질 기록](developer/docs/candidate-quality-verification.md)
