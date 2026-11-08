# 계층 4: 숨은 자동화

relay CLI에 구현돼 있지만 **설치해야만 동작**하는 기능이다. 대부분의 프로젝트에는 설치돼 있지 않다. 사용자가 원할 때만 켠다(세션 동작이 바뀐다).

설치 여부 확인:

```bash
grep -l "relay hook\|relay statusline" .claude/settings*.json ~/.claude/settings*.json 2>/dev/null
```

## 1. 훅 (`relay init --hooks`)

CLAUDE.md의 규칙은 모델이 안 지킬 수도 있다. 훅은 하네스가 실행하므로 항상 적용된다. 통과할 때는 아무것도 출력하지 않는다. 예외가 나면 통과시킨다(fail-open).

| 훅 이벤트 | relay 동작 | 기본값 (`.relay/config.toml`) |
|---|---|---|
| UserPromptSubmit (`prompt_gate`) | 컨텍스트·요청 수가 한도를 넘으면 메시지를 막고 `/handoff` → `/clear` 안내. 막힌 메시지는 모델에 가지 않는다. `[force]`를 넣으면 통과 | `gate_context` 100k, `gate_requests` 100 |
| UserPromptSubmit (보관) | 컨텍스트가 `archive.trigger_context`를 넘으면 대화 원문을 `.relay/archive`에 보관. 새 메시지가 보관된 기록과 겹치면 한 줄 요약을 붙임(recall) | 60k, 요약 최대 400토큰 |
| PreCompact | Claude Code가 대화를 압축하기 직전에 원문 보관(압축이 버린 것을 되찾는 길) | 켜짐 |
| PreToolUse (`read_guard`) | 긴 파일을 범위 없이 Read하거나, 너무 넓은 줄 범위를 읽으면 거부하고 짧은 이유만 돌려줌 | 400줄 넘는 파일, 한 번에 120줄 |
| PreToolUse (`bash_guard`) | 출력이 큰 명령(pytest, git log, npm install, find / …)을 출력 제한 없이 실행하면 거부 | `noisy_commands` 목록 |
| PreToolUse (`write_guard`) | 한 번에 너무 큰 Write 거부 | 6k 토큰 |
| SessionStart (`session_inject`) | 시작, `/clear`, 압축 직후에 brief·state를 자동으로 주입 | startup, clear, compact |

토큰 비용: 공식 문서상 UserPromptSubmit·SessionStart의 평문 출력은 컨텍스트에 들어가고, 출력이 없으면 추가되는 내용이 없다. "통과 시 0토큰"은 이 문서들에서 읽어 낸 추론이다(evidence.md D-hooks).

## 2. 보관과 되찾기 (`relay archive`, `relay recall`)

```bash
relay archive --stats                    # 보관 현황
relay recall 재시도                       # 색인에서 낱말로 찾기
relay recall --open <ID> --query 백오프    # 원문에서 필요한 줄만(최대 800토큰)
relay recall --supersede OLD NEW         # 옛 결정을 새 결정으로 대체 표시
relay recall --retract ID                # 틀린 기록 철회
```

요약만 남기는 대신 원문을 두고 필요한 줄만 꺼낸다(근거 P1, P8).

## 3. statusline (`relay init --statusline`)

Claude Code 하단에 `ctx 72k ████░ · req 31 · read 1.9M · ⚠`처럼 현재 컨텍스트, 요청 수, 누적 캐시 읽기를 보여 준다. statusline은 로컬에서 돌고 API 토큰을 쓰지 않는다(공식 문서). 경고 기준은 `warn_context` 40k, `max_context` 60k, `warn_requests` 30.

## 4. 감시자 (`relay watch`)

훅은 숫자만 본다. "지금 Claude가 하는 일이 Claude가 할 일인가"는 **다른 모델**(codex, 없으면 agy)이 판정한다. 감시에 Claude 토큰을 쓰지 않는다.

판정: `CONTINUE`(계속) · `DELEGATE`(다른 도구로) · `HANDOFF`(상태 파일로 넘길 준비) · `CLEAR`(지금 /clear). 판정은 `.relay/supervisor/<session>.json`에 캐시되고, prompt 훅이 모델 호출 없이 읽는다.

## 5. 자동 순환 (`relay auto "<목표>"`)

Claude를 headless(`claude -p`)로 짧은 청크마다 새 세션으로 실행한다. 청크가 끝날 때 Claude가 state.md에 `STATUS: DONE|CONTINUE`와 다음 단계를 남기고, 감독자가 그 state로 다음 청크를 연다. 사람이 `/clear`·`/pickup`을 칠 필요가 없다. 컨텍스트가 한도를 넘으면 PreToolUse 훅이 state 쓰기 외 도구를 막고 마무리를 지시한다.

## 6. 원격 실행 (선택)

- `relay request <id> image|advise|delegate`: 직접 접근할 수 없는 VM의 codex·agy에 git으로 요청을 보내고, VM 워커(`relay inbox`)가 처리해 결과를 푸시한다.
- `relay cloud-run`: GitHub 호스팅 러너용 요청 검증·감사 산출물(모델 호출 없음).

## 끄기

`.claude/settings.json`에서 해당 훅 항목을 지우거나, `.relay/config.toml`의 `[hooks] enabled` 목록에서 뺀다.
