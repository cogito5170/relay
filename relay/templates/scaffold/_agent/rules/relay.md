# Relay — Antigravity 역할: 탐색 · 대량 읽기 · 기계적 구현

저장소 루트의 `AGENTS.md` 프로토콜을 따른다.
`docs/agent-guides/agy.md`가 있으면 실행 예시와 runner 주의사항도 따른다.

## 탐색 (`explore <task-id>`)
1. `.relay/tasks/<task-id>/brief.md` 를 읽고 관련 코드를 넓게 탐색한다. 이 단계에서는 컨텍스트를 아끼지 않아도 된다.
2. `.relay/tasks/<task-id>/explore.md` 를 작성한다 (relay MCP 가 연결돼 있으면 `relay_task_write(file="explore")`):
   - `## Summary` — 5줄 이내
   - `## Key locations` — `path:start-end — 왜 중요한가` 목록 (최대 15개, 구간은 꼭 필요한 만큼만 좁게)
   - `## Risks` — 깨지기 쉬운 곳, 숨은 의존성
3. 탐색 단계에서는 코드를 수정하지 않는다.

## 구현 (`implement <task-id>`)
- `brief.md` 와 `state.md` 의 Next 를 따른다. 끝나면 `out-antigravity.md` 를 쓴다.
