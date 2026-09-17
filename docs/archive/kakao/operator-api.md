# 경량 버전 최종 제출 계약

이 계약은 **founder-light-1 계약**입니다. 기존 main v4와 별도이며, 운영측 수신부는 이 문서의
이메일·코드 헤더 인증을 구현해야 합니다.
최종 제출 주소는 `https://hr-training-13-209-237-72.sslip.io/api/lite`로 설정했습니다.
운영측 소스는 확인했고 실제 배포 상태는 [연결 기록](live-connection.md)을 따릅니다.
`operator.base_url`은 상태 확인용 기본 주소, `operator.url`은 최종 제출 전체 주소입니다.
참가자 로그인 endpoint나 Bearer 토큰은 사용하지 않습니다. 운영측에 등록된 이메일과 해당 이메일의
6자리 코드를 최종 제출 요청 헤더로 전달하고, 운영측이 행사와 참가자를 확인합니다.
하네스는 두 값을 프로세스 환경에만 두며 세션 파일이나 Claude 입력에 저장하지 않습니다.
면접·피드백 원문을 회차마다 전송하지 않습니다.

## 요청

설정의 `operator.url` 전체 주소로 `POST`합니다. 원격 주소는 HTTPS,
로컬 수신부 검사에는 `http://127.0.0.1`도 허용합니다. 리다이렉트는 따르지 않습니다.

```http
Content-Type: application/json; charset=utf-8
X-Participant-Email: participant@example.org
X-Participant-Code: 123456
X-Participant-Workshop: workshop
Idempotency-Key: <session_id>:final
```

본문은 `python -m harness schema`로 출력하는 JSON Schema를 따릅니다.
실제 전송 예시는 각 세션의 `final-payload.json`에 저장됩니다.

운영측은 세 헤더의 이메일·코드·행사를 하나의 참가자 계정에 대조합니다. 이메일이나 코드가
틀리면 `401` 또는 `403`을 반환하고 저장하지 않습니다. 코드는 6자리 숫자이며 프록시·접속
로그에 남기지 않아야 합니다. 인증에 성공한 뒤에만 payload와 멱등 키를 처리합니다.

| 필드 | 의미 |
| --- | --- |
| `schema_version` | `founder-light-1` |
| `session_id` | init에서 만든 UUID, 세션 내 고정 |
| `submission_id` | `<session_id>:final`, 멱등 키와 동일 |
| `insights.markdown` | 네 번째 피드백 후 최종 MD 원문 |
| `interviews` | 순서대로 4명, 첫 mode=human·1800초, 나머지 agent·1200초 |
| `ranking` | 1~4위, 네 후보를 각각 한 번 포함 |
| `payload_sha256` | 이 필드 자체를 제외한 전체 본문의 canonical JSON SHA-256 |

`interviews`에는 이름·후보 ID·시각·최대 시간·인사이트 수정 여부·기준 전후 해시·
원문/평가 해시·진행 예외 메모가 있습니다. Unix 초를 사용합니다.
`ranking` 항목은 `rank`, `candidate_id`, `rationale`, `evidence`, `uncertainty`입니다.
근거에는 `source_id`, 실제 원문에서 연속 인용한 `quote`가 있습니다.
`P01:message:<기록ID>`는 해당 후보 발언, `P01:feedback`은 사용자 평가입니다.
computer use의 `cu-observed-*`는 로컬 화면 관찰 ID이며 카카오 원본 ID가 아닙니다.
구버전의 원본 ID·Windows 관찰 ID는 기존 원문과 함께 보존합니다.
실제 후보 외의 후보, 중복 순위, 다른 후보 인용, 면접관 질문 인용, 원문에 없는 인용을 거부합니다.

운영측의 새 수신 계약은 `insights`에 `markdown`만 허용합니다. 이전 제안의 `insights.sha256`은
전송하지 않으며, 서버가 원문 해시를 계산해 네 번째 면접의 `insights_after_sha256`와 비교합니다.
원문은 최대 100,000자, 요청 전체는 최대 1 MB입니다. 실제 종료 안내가 마감보다 늦으면
그 지연을 `protocol_note`에 기록합니다. 이미 구계약으로 확정된 본문·멱등 키는 자동 변경하지 않습니다.

canonical JSON은 Python의 `json.dumps(value, ensure_ascii=False, sort_keys=True,
separators=(",", ":"))`를 UTF-8로 인코딩합니다. 서버는 전송된 숫자 표현을 보존하거나
같은 정규화 규칙으로 해시를 확인해야 합니다.

## 응답과 재시도

정상 접수 시 HTTP 200과 **아래 세 필드만** 반환합니다.

```json
{
  "accepted": true,
  "session_id": "요청의 session_id",
  "payload_sha256": "요청의 payload_sha256"
}
```

세션과 해시가 일치해야 참가자가 완료로 저장합니다. HTTP 200만으로는 완료가 아닙니다.
동일 키·동일 본문은 최초 접수 결과를 반환하고, 같은 키·다른 본문은 409로 거부해야 합니다.
서버가 접수한 직후 연결이 끊긴 경우에도 재시도로 접수가 중복되지 않아야 합니다.
참가자도 최초 요청 전에 본문과 목적지를 확정하고, 재시도 시 모델을 다시 호출하거나
현재 MD를 다시 반영하지 않습니다. 접수 후 별도 결과 조회·추가 학습·추가 제출은 없습니다.

사용자 평가와 전체 면접 원문은 로컬 보관합니다. 최종 순위에 필요한 인용과 해시만 전송됩니다.
대화 상대에게 보내는 카카오 메시지와 이 API 제출은 별도 경로입니다.
