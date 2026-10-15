# Claude Code guide

## Role

Claude는 설계 판단, 보안 검토, 어려운 디버깅과 최종 리뷰를 담당한다.
넓은 탐색과 기계적 변경은 직접 반복하지 말고 Agy 탐색 또는 Codex 구현
결과를 사용한다.

## Resume a task

Claude Code에서:

```text
/pickup <task-id>
```

명령을 쓸 수 없으면 다음 세 파일만 먼저 읽는다.

```text
.relay/tasks/<task-id>/brief.md
.relay/tasks/<task-id>/state.md
.relay/tasks/<task-id>/pack.md
```

## Read-only advice

터미널에서 실행할 때:

```bash
relay advise <task-id> --agent claude
```

직접 비대화형으로 실행할 때:

```bash
claude -p --model sonnet \
  --permission-mode plan \
  --max-turns 4 \
  "AGENTS.md와 task <task-id>의 brief/state/pack을 읽고 검토하라. 파일은 수정하지 마라."
```

## Review contract

리뷰 결론은 반드시 다음 중 하나로 끝낸다.

```text
VERDICT: APPROVE
VERDICT: CHANGES
```

`CHANGES`이면 문제, 근거 `path:start-end`, 최소 수정 방향을 적는다.
리뷰가 끝나면 `.relay/tasks/<task-id>/out-claude.md`를 갱신한다.

## Handoff

작업을 멈추거나 세션을 바꿀 때:

```text
/handoff <task-id>
```

MCP가 있으면 `relay_task_write(file="state")`를 사용한다. `state.md`는
`Done`, `Decisions`, `Next`, `Files`만 포함하고 250단어를 넘기지 않는다.

## Safety

- OAuth 인증 파일과 환경변수를 읽거나 출력하지 않는다.
- AWS runner에서 `sudo claude`를 실행하지 않는다.
- 명시적 승인 없이 write mode, 배포, push를 수행하지 않는다.
- Port의 사용자 입력을 shell 문자열로 조합하지 않는다.

