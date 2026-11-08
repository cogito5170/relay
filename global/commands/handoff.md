---
description: 현재 작업 상태를 다음 세션용 파일로 저장하고 /clear 준비
argument-hint: [task-id]
---
다음 세션이 이어받을 상태를 파일로 남긴다. 일의 내용은 카드에, 세션 문맥만 state 에 둔다.

형식·예시·스크립트 설명은 relay 스킬(`~/.claude/skills/relay/`)에 있다. 형식이 헷갈릴 때만 `reference/state-format.md` 를 읽는다.

## 0. 모드
- 프로젝트에 `tasks/C-*.md` 가 있으면 **카드 모드**: 1 → 2 → 3(카드) → 4 → 5.
- 없으면 **글 모드**: 1 → 2 → 3(글) → 4 → 5.

## 1. 대상 결정 (`<id>`, `<target>`)
1. `relay_task_write` 도구가 있으면 `file: "state"` 로 쓴다. `<id>` = `$ARGUMENTS`.
2. 아니고 `.relay/tasks/` 가 있으면:
   - `$ARGUMENTS` 가 있으면 `<id>` = `$ARGUMENTS`.
   - 비어 있으면 `<id>` = `find .relay/tasks -name state.md -exec ls -t {} + | head -1` 결과의 폴더 이름. 하나도 없으면 사용자에게 id 를 묻는다.
   - `<target>` = `.relay/tasks/<id>/state.md`
3. 둘 다 아니면 `<id>` 없음, `<target>` = `.relay/HANDOFF-NEXT.md` (없으면 만든다).

## 2. 백업
`<target>` 이 이미 있으면 같은 폴더의 `<이름>.prev.md` 로 복사한 뒤 덮어쓴다 (한 단계만 보관).

## 3(카드). 카드 갱신 후 포인터 state 작성
이번 세션에서 손댄 카드마다:
- `done_when` 을 실행한다. 종료 코드 0 이면 `status: done`. 모델의 판단으로 done 을 쓰지 않는다. `done_when: human` 은 사람이 확인했다고 말한 경우에만 done.
- 실패했고 다음 세션이 이어서 하면 `doing`, 막혔으면 `blocked`.
- 본문 `## 결정 · 버린 것 · 다음 · 지뢰` 에 이번 세션 내용을 날짜와 함께 덧붙인다.
- 새로 생긴 일은 `tasks/_template.md` 형식으로 새 카드를 만든다 (다음 번호).
- 이번 세션의 검출·개입 사건을 해당 카드 로그에 태그로 남긴다: `[catch]` checker·시뮬레이션이 잡은 오류, `[ai-err]` AI 오류(누가 잡았나), `[human]` 사람 결정·수정(결과를 바꿨나).
- 장부 기록(사용자가 켠 프로젝트만): `.relay/ledger/` 가 있으면 프로젝트 루트에서 `python3 ~/.claude/skills/relay/scripts/harvest.py` 를 실행한다. `.relay/ledger/raw/` 에는 사용자 입력 원문이 들어가므로 `.gitignore` 에 있는지 먼저 확인한다 (세션·카드·checker 기록 → `.relay/ledger/`). 프로젝트에 checker 가 있으면 `--check "<명령>"` 을 붙인다. 프로젝트에 `projects/R4_research_pipeline/harvest.py` 가 있으면 그것을 대신 쓴다.
- `python3 ~/.claude/skills/relay/scripts/check_routing.py` 로(프로젝트에 `projects/R4_research_pipeline/check_routing.py` 가 있으면 그것으로), 이번 세션에서 손댄 카드 중 `model` 이 `opus`·`inherit` 가 아닌 카드마다 `--expect "<C-id>=<model>"` 을 붙여 한 번에 실행한다. FAIL 이면 그 카드 로그에 `[ai-err] routing: <C-id> ran <실제 모델>` 을 남긴다.

그다음 `<target>` 을 아래 형식으로 쓴다. 공백으로 나눈 단어 기준 180개 이내. 일의 내용은 쓰지 않는다:

```
STATUS: CONTINUE
META: <YYYY-MM-DD HH:MM> · <branch>@<short-hash> · uncommitted <N> files
ACTIVE: <C-xxx 또는 none>
## Waiting
- <항목> | check: <셸 명령, 종료 0 = 이미 해결됨>
- <항목> | human
## Uncommitted
(커밋 안 된 경로)
## Session
(카드에 안 맞는 세션 문맥: 환경 문제, 중단 이유 등. 없으면 비움)
```

- Waiting 항목은 한 줄에 하나. 기계로 해결 여부를 확인할 수 있으면 `check:`, 사람만 풀 수 있으면 `human`.
- `blocked` 라고 쓰는 줄에는 `| cause: <원인> | evidence: <오류 한 줄 또는 명령 출력>` 을 붙인다. 원인을 모르면 blocked 라고 쓰지 말고 "원인 조사 필요" 로 쓴다.
- 모든 카드가 done 이면 첫 줄을 `STATUS: DONE`.

## 3(글). 글 state 작성
공백으로 나눈 단어 기준 250개 이내, 아래 형식만 사용:

```
STATUS: CONTINUE
META: <YYYY-MM-DD HH:MM> · <branch>@<short-hash> · uncommitted <N> files
## Done
## Decisions
(결정 + 이유 한 줄씩)
## Next
(새 세션이 바로 실행할 수 있는 단계)
## Files
(`path:start-end`)
```

- 끝났으면 첫 줄을 `STATUS: DONE`.

META 값은 `date '+%F %R'`, `git rev-parse --abbrev-ref HEAD`, `git rev-parse --short HEAD`, `git status --porcelain | wc -l` 로 채운다. git 저장소가 아니면 `no-git`.

## 4. 검증
쓴 파일을 다시 읽어 확인하고, 어긋나면 고쳐서 다시 쓴다:
- 첫 줄이 `STATUS: CONTINUE` 또는 `STATUS: DONE`, 둘째 줄이 `META:`
- 카드 모드: `ACTIVE:` 줄과 `## Waiting`, `## Uncommitted`, `## Session` 이 있음. ACTIVE 카드 파일이 존재함. 각 카드의 `status` 가 todo·doing·done·blocked 중 하나
- 글 모드: `## Done`, `## Decisions`, `## Next`, `## Files` 가 있고 Files 의 각 경로가 존재함 (`test -e`)
- 단어 수 한도 이내 (`wc -w`)
- 카드 모드이면 `python3 ~/.claude/skills/relay/scripts/check_handoff.py <target>` 을 실행한다(프로젝트에 `projects/R4_research_pipeline/check_handoff.py` 가 있으면 그것으로). FAIL 이면 고쳐서 다시 쓴다. STALE 항목은 지우고, 원인 없는 blocked 항목은 원인을 확인해 적거나 "원인 조사 필요" 로 바꾼다.

## 5. 출력
한 줄만: `state 저장됨 — /clear 후 /pickup <id>`. `<id>` 가 없으면(1-3 경로) `/pickup` 만 쓴다.
