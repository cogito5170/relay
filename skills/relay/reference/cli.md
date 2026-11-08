# 계층 3: relay CLI·MCP

여러 AI 도구(ChatGPT, Antigravity `agy`, Codex CLI, Claude Code)를 **같은 task 폴더**로 묶는 명령줄 도구다. 비싼 토큰은 판단이 필요한 곳(Claude)에만 쓰고, 탐색·기계적 구현은 다른 도구로 넘긴다.

## 설치 확인

```bash
which relay            # 보통 ~/.local/bin/relay
relay check --ping     # claude · codex · agy 설치·응답 진단
```

없으면 relay 소스 저장소에서 `pip install -e .` (Python 3.11+, 외부 의존성 없음). 소스 위치는 사용자에게 묻는다.

## 프로젝트에 설치

```bash
relay init                       # AGENTS.md · CLAUDE.md · .agent/ · .claude/commands · .relay/ 생성. 기존 파일은 건드리지 않음(--force 로 덮어씀)
relay init --mcp                 # + Claude Code 용 .mcp.json
relay init --statusline --hooks  # + 계층 4 (hidden.md). 사용자 동의 후에만
relay mcp-config codex           # ~/.codex/config.toml 에 붙일 스니펫
relay mcp-config antigravity     # Antigravity MCP 설정에 병합할 스니펫
```

## task 폴더 `.relay/tasks/<id>/`

| 파일 | 쓰는 쪽 | 읽는 쪽 |
|---|---|---|
| `brief.md` | 사람 + 기획 도구 | 전원 |
| `explore.md` | 탐색 담당(agy). `path:start-end` 목록, 최대 15개 | `relay pack --refs` |
| `pack.md` | `relay pack` | Claude, Codex |
| `state.md` | 진행 중인 에이전트(`/handoff`) | 다음 세션(`/pickup`) |
| `out-<agent>.md` | 각 에이전트, 300단어 이내 | 사람, 다음 단계 |
| `prompt-<agent>.md` | `relay handoff --to` | MCP 없는 도구(붙여넣기) |

`.relay/`는 기본적으로 git에서 빠진다. 결과를 공유하려면 `git add -f`.

## 주요 명령

| 명령 | 하는 일 |
|---|---|
| `relay task new "<제목>"` / `relay task list` | task 폴더 생성, 추천 도구 표시 |
| `relay route "<설명>"` | 키워드 규칙으로 맡길 도구 추천(`.relay/config.toml`로 조정) |
| `relay pack [id] --refs explore.md --full G --outline G --diff [REF] --budget N` | 예산(기본 12k 토큰) 안에서 필요한 조각만 모은 pack.md. 넘치는 파일은 시그니처만 남기고 강등 |
| `relay handoff [id] --to chatgpt\|antigravity\|codex\|claude [--copy]` | 도구별 인수인계 프롬프트 |
| `relay run [id] [--verify CMD] [--dry-run]` | 파이프라인: explore(agy) → pack → implement(Codex) → verify → review(Claude). 리뷰가 CHANGES면 재구현(최대 2회) |
| `relay advise [id] --agent claude\|agy` | brief+pack만 읽는 읽기 전용 자문 → `out-claude.md`/`out-agy.md` |
| `relay delegate codex\|agy "<지시>"` | 위임하고 마지막 N줄만 돌려받음(나머지는 로그 파일) |
| `relay lean` | 도구를 줄인 대화형 Claude(첫 요청 컨텍스트 약 −78%, 원 저장소 실측) |

## 측정 명령

| 명령 | 하는 일 |
|---|---|
| `relay usage [--days N] [--save-baseline]` | Claude Code·Codex 로컬 로그로 토큰 사용량, 기준선 대비 배수 |
| `relay estimate --requests 100 --sessions 1 --offload 0.5 --after-sessions 2` | 세션 분할·오프로드 효과 계산 |
| `relay why [session]` | 한 세션의 캐시 읽기를 원인별로 분해(고정 부분, thinking, 명령 출력 …)하고 처방 |
| `relay doctor` | 고정 부분 진단: CLAUDE.md·@import 크기, MCP 서버 목록, 주간 절감량 |
| `relay events [--tail N]` | MCP 도구 호출 감사 로그 요약 |

## MCP 서버 (`relay mcp`)

같은 task 폴더를 MCP 도구로 노출해 복붙을 없앤다. 표준 라이브러리로 만든 stdio JSON-RPC 서버.

| 도구 | 하는 일 |
|---|---|
| `relay_task_list` / `relay_task_read` / `relay_task_write` | 목록, brief·state·pack 한 번에 읽기, 쓰기 |
| `relay_task_create`, `relay_pack`, `relay_route` | 생성, pack, 추천 |
| `relay_handoff`, `relay_usage` | full 프로필만 |

프로필별 최소 권한(쓸 수 있는 파일):

| 프로필 | 기본 클라이언트 | 쓰기 허용 |
|---|---|---|
| `claude` | Claude Code | brief, state, out-claude |
| `explorer` | Antigravity | explore, state, out-antigravity |
| `builder` | Codex | state, out-codex |
| `full` | — | brief, state, explore, out-* |

- 도구 정의도 매 요청 토큰이다. `relay mcp --cost`로 프로필별 비용을 본다.
- 프로젝트 루트 밖 경로는 읽지 않는다. 모든 호출은 `.relay/events.jsonl`에 남는다.

```text
Antigravity › 003 탐색해서 explore 남겨줘   → relay_task_read → relay_task_write(file="explore")
Codex       › 003 구현해줘                 → relay_task_read → (구현) → relay_task_write(file="out-codex")
Claude      › /pickup 003                 → relay_task_read
```

## 역할 분담 (기본값)

| 도구 | 맡는 일 |
|---|---|
| ChatGPT | 아이디어 → brief, 리서치, 문서 |
| Antigravity | 넓은 탐색, 큰 파일 읽기, 위치 수집(탐색 중 수정 금지) |
| Codex | 보일러플레이트, 테스트, 반복 리팩터링 |
| Claude | 설계 결정, 원인 불명 버그, 보안·동시성, 최종 리뷰(`VERDICT: APPROVE` / `VERDICT: CHANGES`) |

## 도구 간 공통 진입점

- Codex와 Antigravity는 저장소 루트 `AGENTS.md`를 읽는다. Claude Code는 CLAUDE.md가 없을 때 AGENTS.md를 읽고, 있으면 CLAUDE.md 안에서 `@AGENTS.md`로 가져온다(D: docs.md).
- `relay init`이 만드는 AGENTS.md에 "task id를 받으면 brief → state → pack만 먼저 읽는다"를 넣어 세 도구가 같은 순서로 시작하게 한다.
