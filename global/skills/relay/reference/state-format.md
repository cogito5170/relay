# 계층 1: 상태 파일 형식

`/handoff`(쓰기)와 `/pickup`(읽기)이 쓰는 파일이다. 명령 본문은 `~/.claude/commands/handoff.md`, `pickup.md`에 있고, 이 문서는 그 형식과 예시를 설명한다.

## 공통 머리 두 줄

```text
STATUS: CONTINUE            # 끝났으면 STATUS: DONE
META: 2026-01-15 14:30 · main@a1b2c3d · uncommitted 3 files
```

META 값은 다음 명령으로 채운다. git 저장소가 아니면 `no-git`.

```bash
date '+%F %R'; git rev-parse --abbrev-ref HEAD; git rev-parse --short HEAD; git status --porcelain | wc -l
```

`/pickup`은 META의 브랜치·해시가 지금 HEAD와 다르면 한 줄로 경고한다(다른 곳에서 커밋이 생겼다는 신호).

## 글 모드 (카드가 없는 프로젝트, 250단어 이내)

```text
STATUS: CONTINUE
META: 2026-01-15 14:30 · main@a1b2c3d · uncommitted 2 files
## Done
- 로그인 폼 검증 추가, 단위 테스트 통과
## Decisions
- 세션 토큰은 쿠키가 아니라 메모리에 둔다 — XSS 위험이 더 작다
## Next
1. src/auth/refresh.ts 에 토큰 갱신 추가
2. npm test -- auth 로 확인
## Files
- src/auth/login.ts:40-88
- tests/auth/login.test.ts:1-60
```

- `Files`의 각 경로는 존재해야 한다(`test -e`). 줄 범위(`path:start-end`)를 쓰면 다음 세션이 그 부분만 읽는다.
- `Next`는 새 세션이 바로 실행할 수 있는 단계로 쓴다.

## 카드 모드 (카드가 있는 프로젝트, 180단어 이내)

일의 내용은 카드에 있으므로 상태 파일은 **포인터와 세션 문맥만** 담는다.

```text
STATUS: CONTINUE
META: 2026-01-15 14:30 · main@a1b2c3d · uncommitted 4 files
ACTIVE: C-007
## Waiting
- 배포 대상 리전 결정 | human
- 마이그레이션 적용 여부 | check: test -f db/migrations/0042_applied
- 외부 API 키 발급 | human | blocked | cause: 관리자 승인 대기 | evidence: 401 Unauthorized (curl /v1/me)
## Uncommitted
- src/api/handler.ts
- tasks/C-007.md
## Session
- 로컬 DB 컨테이너가 5432 포트 충돌로 5433 사용 중
```

규칙:

- `ACTIVE`는 진행 중 카드 id 또는 `none`. 그 카드 파일이 존재해야 한다.
- `Waiting` 한 줄에 하나.
  - 기계로 확인할 수 있으면 `| check: <셸 명령>` — 종료 코드 0이면 **이미 해결됨**. 검사기가 해결된 항목을 STALE로 잡아 실패시킨다.
  - 사람만 풀 수 있으면 `| human`.
  - `blocked`라고 쓰면 반드시 `| cause: … | evidence: <오류 한 줄 또는 명령 출력>`. 원인을 모르면 "원인 조사 필요".
- 모든 카드가 done이면 `STATUS: DONE`.

## `/pickup`이 읽은 뒤 하는 일

1. `STATUS: DONE`이면 요약만 보고하고 멈춘다.
2. META가 HEAD와 다르면 경고한다.
3. `## Waiting`이 비어 있지 않으면 **그 질문부터** 사용자에게 한다.
4. 카드 모드면 카드를 고르고([card-mode.md](card-mode.md)), 글 모드면 `Next` 첫 단계부터 진행한다. `Files`에 없는 정보가 확실히 필요할 때만 범위를 좁혀 더 읽는다.

## `/handoff` 검증 (쓴 뒤 다시 읽어 확인)

- 첫 줄 `STATUS: CONTINUE|DONE`, 둘째 줄 `META:`
- 모드별 필수 절이 있음, 단어 수 한도(`wc -w`) 이내
- `python3 ~/.claude/skills/relay/scripts/check_handoff.py <파일> --words 180` 이 PASS (카드 모드)
- 출력은 한 줄: `state 저장됨 — /clear 후 /pickup <id>`

## 작성 요령 (근거: [evidence.md](evidence.md))

- **앞과 끝에 중요한 것.** 긴 입력의 중간은 잘 쓰이지 않는다(P4). `Waiting`·`Next`를 앞쪽에 둔다.
- **줄이지 말아야 할 것은 줄이지 않는다.** 매번 통째로 다시 요약하면 세부가 닳는다(P7, 맥락 붕괴). 쌓여야 하는 결정은 카드의 결정 로그에 **덧붙이고**, 상태 파일은 그 위치를 가리킨다.
- **세부는 원문에 두고 포인터만.** 요약만 남기면 정보를 잃는다(P1, P8). `path:start-end`, 카드 id, archive id로 가리킨다.
