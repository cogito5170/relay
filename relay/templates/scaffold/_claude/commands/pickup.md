---
description: 저장된 task 상태로 새 세션 재개
argument-hint: <task-id>
---
`.relay/tasks/$ARGUMENTS/` 의 `brief.md`, `state.md`, `pack.md` 중 존재하는 것을 **한 번에 병렬로** 읽고, state 의 Next 첫 단계부터 진행하라.
pack 에 없는 정보가 확실히 필요할 때만 범위를 좁혀 추가로 읽는다.
