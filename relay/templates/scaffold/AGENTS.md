# Agent relay protocol

이 저장소는 여러 AI 도구(ChatGPT · Antigravity · Codex · Claude)가 `.relay/tasks/<id>/` 의 파일로 작업을 주고받는다.
대화 기록이 아니라 **파일이 인수인계의 단위**다.

| 파일 | 작성자 | 내용 |
|---|---|---|
| `brief.md` | 사람 + ChatGPT | 목표 · 제약 · 완료 조건 |
| `explore.md` | Antigravity | 탐색 리포트 (`path:start-end` 참조 목록) |
| `pack.md` | `relay pack` | 다음 에이전트가 읽을 최소 컨텍스트 |
| `state.md` | 진행 중인 에이전트 | 끝낸 일 / 결정 / 다음 단계 |
| `out-<agent>.md` | 각 에이전트 | 산출물 요약 |

## 규칙
- `relay_task_read` / `relay_task_write` MCP 도구가 연결돼 있으면 파일을 직접 다루는 대신 그것을 쓴다 (권한 검사 · 감사 로그).
- task id 와 함께 지시를 받으면 그 폴더의 `brief.md` → `state.md` → `pack.md` 만 먼저 읽는다. 그 밖의 탐색은 꼭 필요할 때만 범위를 좁혀서.
- 코드 위치는 항상 `path:start-end` 형식으로 쓴다 (`relay pack --refs` 가 이 형식을 파싱한다).
- 결과는 `out-<agent>.md` 에 300단어 이내로: 바꾼 것(path:line), 결정과 이유, 남은 일.
- 전체 파일 재출력·장황한 설명 금지. 변경은 편집/diff 로.
