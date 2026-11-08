---
description: 저장된 상태로 새 세션 재개
argument-hint: [task-id]
---
`relay_task_read` 도구가 있으면 `task: "$ARGUMENTS"` 로 한 번 호출하고 아래 "읽은 뒤" 로 간다.

없으면 읽을 곳을 정한다:
1. `.relay/tasks/` 가 있으면:
   - `$ARGUMENTS` 가 있으면 `<id>` = `$ARGUMENTS`.
   - 비어 있으면 `<id>` = `find .relay/tasks -name state.md -exec ls -t {} + | head -1` 결과의 폴더 이름.
   - `.relay/tasks/<id>/` 의 `brief.md`, `state.md`, `pack.md` 중 존재하는 것을 **한 번에 병렬로** 읽는다.
2. 아니면 `.relay/HANDOFF-NEXT.md` 를 읽는다. 인자가 있으면 무시한다.
3. 읽을 state 가 하나도 없으면 그렇게 알리고 멈춘다.

## 읽은 뒤
- `STATUS: DONE` 이면 요약만 보고하고 멈춘다.
- `META` 의 branch·hash 가 현재 `git rev-parse --abbrev-ref HEAD`·`git rev-parse --short HEAD` 와 다르면 한 줄로 경고한다. META 가 없으면 넘어간다.
- `## Waiting` 이 비어 있지 않으면 그 질문부터 사용자에게 한다.

### 카드 모드 (state 에 `ACTIVE:` 가 있고 `tasks/C-*.md` 가 있음)
1. 카드를 고른다: ACTIVE 카드가 `doing` 이면 그것. 아니면 `deps` 가 모두 done 인 `todo` 카드 중 id 가 가장 작은 것. `_template.md` 는 건너뛴다.
2. 착수 전에 그 카드의 `done_when` 을 실행한다. 0 이면 `status: done` 으로 바꾸고 1 로 돌아간다.
3. `needs: human` 이면 시작 전에 사용자 승인을 받는다.
4. `status: doing` 으로 바꾸고, 카드와 `## 입력` 에 적힌 파일만 읽고 진행한다. `scope` 밖은 고치지 않고, `never` 경로는 절대 고치지 않는다.
5. 모델 라우팅: 카드의 `model` 값으로 누가 일할지 정한다. 카드에 적는 것만으로는 모델이 바뀌지 않는다. Agent 호출의 `model` 인자가 가장 우선한다.
   - `opus`, `inherit`, 또는 `model` 없음: 메인 세션이 직접 한다. 여러 파일을 맞물려 고치는 일(문맥 공유가 큰 일)은 카드에 `opus` 를 적는다.
   - `sonnet`, `haiku`, `fable`: 카드 작업을 Agent 로 넘긴다. `model: <카드 값>`, `description` 은 `<C-id>: <goal 요약>` 으로 시작한다. 프롬프트에는 카드 본문, `## 입력` 경로, `scope`·`never` 규칙을 넣는다. 메인은 결과를 받아 `done_when` 을 실행해 확인만 한다.
   - 큰 탐색(여러 파일·웹 문서를 읽고 결론만 필요할 때)은 카드 모델과 상관없이 `scout` agent(`.claude/agents/scout.md`, sonnet)에 맡긴다. `scout` 가 목록에 없으면 general-purpose 에 `model: sonnet` 을 주고 scout.md 규칙을 프롬프트에 넣는다.
6. 고를 카드가 없으면 남은 blocked 카드와 원인을 보고하고 멈춘다.

### 글 모드 (그 밖)
Next 첫 단계부터 진행한다. Files 에 없는 정보가 확실히 필요할 때만 범위를 좁혀 추가로 읽는다.
