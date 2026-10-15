# Antigravity (Agy) guide

## Role

Agy는 넓은 코드 탐색, 관련 위치 수집, 의존성 확인과 기계적 구현을 담당한다.
탐색 단계에서는 파일을 수정하지 않는다.

## Explore a task

Antigravity에서 다음 workflow를 사용한다.

```text
/relay-explore <task-id>
```

명령을 쓸 수 없으면:

1. `.relay/tasks/<task-id>/brief.md`를 읽는다.
2. 관련 파일을 탐색한다.
3. `.relay/tasks/<task-id>/explore.md`를 작성한다.

`explore.md` 형식:

```markdown
## Summary
최대 5줄

## Key locations
- path:start-end — 중요한 이유

## Risks
- 깨지기 쉬운 의존성 또는 보안 경계
```

위치 목록은 최대 15개로 제한하고 반드시 `path:start-end` 형식을 쓴다.
그다음 다른 에이전트가 pack을 만들 수 있도록 안내한다.

```bash
relay pack <task-id> --refs explore.md
```

## Read-only advice

```bash
relay advise <task-id> --agent agy
```

결과는 `.relay/tasks/<task-id>/out-agy.md`에 기록된다.

## Implementation

구현 요청을 받으면 `brief.md`와 `state.md`의 `Next`를 따른다. 변경 후 관련
테스트만 실행하고 `.relay/tasks/<task-id>/out-antigravity.md`에 다음을
300단어 이내로 남긴다.

- 변경한 위치 `path:start-end`
- 결정과 이유
- 실행한 검증
- 남은 일

## Safety

- 탐색 단계에서 수정하지 않는다.
- OAuth token, device code, 인증 파일을 읽거나 출력하지 않는다.
- AWS self-hosted runner에서는 외부 PR 코드를 실행하지 않는다.
- 사용자 입력을 `eval`, `bash -c`, `shell=True`로 실행하지 않는다.

