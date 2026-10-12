---
description: 현재 작업 상태를 state.md 에 저장하고 /clear 준비
argument-hint: <task-id>
---
`.relay/tasks/$ARGUMENTS/state.md` 를 덮어써라 (task id 가 비어 있으면 `.relay/tasks/` 의 가장 최근 폴더).
250단어 이내, 아래 형식만 사용:

## Done
## Decisions
(결정 + 이유 한 줄씩)
## Next
(새 세션이 바로 실행할 수 있는 단계)
## Files
(`path:start-end`)

작성 후 출력은 한 줄만: `state 저장됨 — /clear 후 /pickup <id>`
