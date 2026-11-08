---
name: relay
description: .relay 파일 기반 인수인계 시스템 설명서. 세션을 끊고 이어 가는 상태 파일(HANDOFF-NEXT.md, .relay/tasks/<id>/), 작업 카드(tasks/C-*.md, done_when, 모델 라우팅), relay CLI·MCP, 숨은 자동화(훅, archive/recall, statusline)의 형식·동작·스크립트. 새 프로젝트에 relay를 설치하거나, /handoff·/pickup 형식이 헷갈리거나, 카드·검사기·relay 명령을 다룰 때 쓴다.
---

# relay: 파일 기반 인수인계

## 한 줄 요약

긴 대화 대신 **짧은 파일**로 세션과 도구 사이의 일을 넘긴다. 대화는 버리고(`/clear`), 결정·다음 단계·대기 질문만 파일에 남긴다.

## 왜 이렇게 하나

- 요청 한 번마다 그 시점의 컨텍스트 전체를 다시 읽는다. 세션 비용 ≈ 요청 수 × 고정 부분 + 증가량 × 요청 수² / 2. 세션이 길수록 이차로 늘어난다.
- 그래서 작업 단위마다 `/handoff` → `/clear` → `/pickup`으로 세션을 끊는다. 상태 파일은 사람이 통제하는 수동 압축이다.
- 파일은 모든 도구(Claude Code, Codex, Antigravity)가 읽을 수 있고, 사람이 고칠 수 있고, git에 이력이 남는다.

근거와 출처는 [reference/evidence.md](reference/evidence.md).

## 네 계층 (위에서부터 쓰면 된다)

| 계층 | 무엇 | 언제 쓰나 | 상세 |
|---|---|---|---|
| 1. 상태 파일 | `/handoff`가 쓰고 `/pickup`이 읽는 `STATUS` 파일 | 모든 프로젝트 | [reference/state-format.md](reference/state-format.md) |
| 2. 카드 모드 | `tasks/C-*.md` 작업 카드, 셸 명령 완료 조건, 모델 라우팅 | 여러 세션에 걸친 일 | [reference/card-mode.md](reference/card-mode.md) |
| 3. relay CLI·MCP | `relay` 명령: task 폴더, pack, 도구별 handoff, MCP 서버, 사용량 측정 | 여러 AI 도구를 함께 쓸 때 | [reference/cli.md](reference/cli.md) |
| 4. 숨은 자동화 | 훅(한도 차단, 큰 Read 차단, 자동 보관), statusline, watch, auto | 설치했을 때만 동작 | [reference/hidden.md](reference/hidden.md) |

## 파일 위치 판정 (`/handoff`·`/pickup` 공통)

1. `relay_task_read`/`relay_task_write` MCP 도구가 있으면 그것을 쓴다.
2. `.relay/tasks/`가 있으면 `.relay/tasks/<id>/state.md` (id가 없으면 가장 최근 state.md의 폴더).
3. 둘 다 아니면 `.relay/HANDOFF-NEXT.md`.
4. 프로젝트에 `tasks/C-*.md`가 있으면 **카드 모드**, 없으면 **글 모드**.

쓰기 전에 기존 파일을 `<이름>.prev.md`로 한 단계 백업한다.

## 기본 흐름

```text
/pickup            # 상태 파일을 읽고, Waiting 질문부터 묻고, 다음 단계(또는 다음 카드)로 간다
  … 작업 …
/handoff           # 카드 갱신 → 상태 파일 작성 → 검증 → "state 저장됨 — /clear 후 /pickup"
/clear
```

## 스크립트 (`~/.claude/skills/relay/scripts/`, 표준 라이브러리만)

프로젝트 루트에서 실행한다. 출력과 종료 코드만 보면 되고, 코드는 읽을 필요 없다.

| 스크립트 | 하는 일 | 예시 |
|---|---|---|
| `check_handoff.py` | 상태 파일 형식, 단어 수, `blocked` 줄의 원인·증거, 이미 해결된(`check:`가 0으로 끝나는) Waiting 항목 검사 | `python3 ~/.claude/skills/relay/scripts/check_handoff.py .relay/HANDOFF-NEXT.md` |
| `check_routing.py` | 이 세션과 서브에이전트가 **실제로** 어떤 모델로 돌았는지. 카드의 `model`과 대조 | `python3 …/check_routing.py --expect "C-012=sonnet"` |
| `harvest.py` | 세션 로그·카드 상태·검사기 결과를 `.relay/ledger/`에 누적(멱등). `.relay/ledger/`를 만들어 켠 프로젝트에서만 쓴다. `raw/`에 사용자 입력 원문이 들어가므로 `.gitignore`에 넣는다 | `python3 …/harvest.py --check "python3 checks/x.py"` |

## 하지 말 것

- 상태 파일에 일의 내용을 길게 쓰지 않는다. 내용은 카드나 산출물에, 상태 파일에는 포인터만.
- 모델 판단으로 카드를 `done`으로 바꾸지 않는다. `done_when` 명령의 종료 코드 0만 완료다.
- 원인을 모르면서 `blocked`라고 쓰지 않는다. "원인 조사 필요"로 쓴다.
- 숨은 자동화(계층 4)를 사용자 동의 없이 설치하지 않는다. 훅은 세션 동작을 바꾼다.
