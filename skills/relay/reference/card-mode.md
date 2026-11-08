# 계층 2: 카드 모드

여러 세션에 걸친 일을 **작업 카드** 파일로 쪼갠다. 상태 파일은 어떤 카드가 진행 중인지만 가리키고, 일의 내용·결정·지뢰는 카드에 쌓인다.

## 카드 형식 (`tasks/C-<번호>.md`, 템플릿은 `tasks/_template.md`)

```markdown
---
id: C-012
goal: 결제 웹훅 실패 시 지수 백오프로 3회 재시도하고, 실패 이벤트를 대기열에 남긴다
done_when: pytest -q tests/webhook/test_retry.py && python3 checks/webhook_contract.py
scope: [src/webhook/, tests/webhook/, tasks/C-012.md]
never: [CLAUDE.md, SPEC.md, src/billing/]
deps: [C-010]
needs: none
model: sonnet
budget: 2
status: todo
---
## 왜
## 입력
src/webhook/handler.py · docs/webhook.md
## 경계
## 출력
## 결정 · 버린 것 · 다음 · 지뢰
- 2026-01-15: 카드 생성.
- 2026-01-16 [catch] 계약 검사기가 재시도 헤더 누락을 잡음.
- 2026-01-16 [ai-err] 에이전트가 never 경로를 고치려 함 — 메인이 되돌림.
- 2026-01-17 [human] 사용자가 재시도 횟수를 5→3으로 결정(결과 바뀜).
```

| 필드 | 뜻 |
|---|---|
| `goal` | 한 문장 끝 상태 |
| `done_when` | 종료 코드 0이면 완료인 셸 명령. 명령으로 못 쓰면 `human` |
| `scope` / `never` | 고쳐도 되는 경로 / 절대 고치면 안 되는 경로(승인된 명세서는 `never`에) |
| `deps` | 이 카드들이 done이어야 시작 |
| `needs` | `human`이면 시작 전에 사람 승인 |
| `model` | 누가 일하는지(아래 라우팅) |
| `budget` | 세션당 최대 비용(USD) |
| `status` | `todo` · `doing` · `done` · `blocked` |

## `/pickup`의 카드 선택

1. `ACTIVE` 카드가 `doing`이면 그것. 아니면 `deps`가 모두 done인 `todo` 중 id가 가장 작은 것. `_template.md`는 건너뛴다.
2. **착수 전에 `done_when`을 실행한다.** 0이면 `status: done`으로 바꾸고 1로 돌아간다(이미 끝난 일을 다시 하지 않는다).
3. `needs: human`이면 사용자 승인부터 받는다.
4. `status: doing`으로 바꾸고, 카드와 `## 입력`의 파일만 읽는다. `scope` 밖은 고치지 않고 `never`는 절대 고치지 않는다.
5. 고를 카드가 없으면 남은 blocked 카드와 원인을 보고하고 멈춘다.

## 모델 라우팅

카드에 `model`을 적는 것만으로는 모델이 바뀌지 않는다. **Agent 호출의 `model` 인자**가 실제로 정한다.

| 카드 `model` | 누가 하나 |
|---|---|
| `opus`, `inherit`, 없음 | 메인 세션이 직접. 여러 파일을 맞물려 고치는 일(문맥 공유가 큰 일)은 `opus`로 적는다 |
| `sonnet`, `haiku` 등 | Agent로 넘긴다. `model: <카드 값>`, description은 `<C-id>: <goal 요약>`으로 시작. 프롬프트에 카드 본문, `## 입력` 경로, `scope`·`never`를 넣는다. 메인은 결과를 받아 `done_when`으로 확인만 한다 |
| (카드와 무관) 큰 탐색 | 여러 파일·웹 문서를 읽고 결론만 필요하면 탐색 전용 에이전트(sonnet)에 맡긴다. 프로젝트에 `.claude/agents/scout.md`가 있으면 그것을, 없으면 general-purpose에 `model: sonnet` |

실제로 그 모델이 돌았는지는 `check_routing.py`로 확인한다.

```bash
python3 ~/.claude/skills/relay/scripts/check_routing.py --expect "C-012=sonnet"
# FAIL이면 카드 로그에: [ai-err] routing: C-012 ran <실제 모델>
```

## `/handoff`의 카드 갱신

이번 세션에서 손댄 카드마다:

1. `done_when` 실행 → 0이면 `done`. 모델 판단으로 done을 쓰지 않는다. `done_when: human`은 사람이 확인했다고 말한 경우만.
2. 이어서 할 일이면 `doing`, 막혔으면 `blocked`.
3. `## 결정 · 버린 것 · 다음 · 지뢰`에 날짜와 함께 **덧붙인다**(지우거나 다시 쓰지 않는다).
4. 새 일은 다음 번호로 새 카드.
5. 사건 태그를 남긴다:
   - `[catch]` 검사기·시뮬레이션이 잡은 오류
   - `[ai-err]` AI 오류(누가 잡았나)
   - `[human]` 사람 결정·수정(결과를 바꿨나)
6. `harvest.py`로 장부에 기록한다(선택).

## 검사기(checker) 관례

- 검사기는 **먼저** 만들고, 실패(아니오)를 확인한 뒤 착수한다. 그래야 검사기가 실제로 무언가를 잡는다는 것이 보인다.
- 출력은 줄마다 `예  [규칙] …` / `아니오  [규칙] …`, 모두 예일 때만 종료 코드 0. `harvest.py --check`가 이 형식을 센다.
- 검사기 파일은 그 카드의 `never`에 넣어, 일하는 쪽이 기준을 고치지 못하게 한다.

## 선택: 문제 기록 파일 관례

일하다 생긴 문제를 한 파일에 쌓는 형식. 프로젝트 CLAUDE.md에서 켠다.

- 맨 위 요약 표 `# | 단계 | 문제 | 결과`, 본문은 번호 붙은 절.
- 각 절: **상황 → 원인 → 해결 → 근거 → 확인 상태**(필요하면 재발 방지).
- 원인을 확인하지 않았으면 "가설, 아직 확인 안 함".
- 확인 상태에는 "확인함"과 "확인 안 함"을 둘 다 쓴다.
- 근거에 관련 카드 id(`C-0nn`)를 단다. 새 일반 원칙은 "배운 점"에 한 줄.
