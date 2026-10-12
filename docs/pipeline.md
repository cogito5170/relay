# 토큰 절감 파이프라인 (`relay run`, `relay lean`)

## 핵심 발견: 고정 부분의 대부분은 "도구 정의"다

빈 프로젝트에서 `claude -p "Reply with exactly: OK"` (Haiku, 1턴)의 첫 요청 컨텍스트를 플래그별로 측정했다 (Claude Code 2.1.296).

| 설정 | 첫 요청 컨텍스트 | 기본 대비 |
|---|---|---|
| 기본 | 35.1k | — |
| `--strict-mcp-config` (MCP 끔) | 35.1k | 0% (이 환경엔 MCP 없음) |
| `--tools Read,Edit,Write,Bash,Grep,Glob` (대화형 lean) | **7.9k** | **−78%** |
| `--tools Read,Grep,Glob` (리뷰 전용) | 6.1k | −83% |
| + 짧은 `--system-prompt` | **4.0k** | **−89%** |

고정 부분은 **매 요청** 다시 읽히므로, 이 절감은 세션의 모든 요청에 그대로 곱해진다.
(`--bare` 는 더 줄지만 API 키 인증만 허용해서 구독 사용자는 쓸 수 없다.)

## 1. `relay lean` — 매일 쓰는 대화형 세션

```bash
relay lean                     # = claude --tools Read,Edit,Write,Bash,Grep,Glob --strict-mcp-config [--mcp-config .mcp.json]
relay lean -- --model sonnet   # 뒤 인자는 claude 에 그대로
relay lean --print             # 실행할 명령만 확인
```

- 빠지는 도구: WebFetch · WebSearch · 서브에이전트(Task) · TodoWrite · NotebookEdit 등. 웹 조사는 ChatGPT, 넓은 탐색은 Antigravity 가 맡는 역할 분담과 맞는다.
- 필요하면 `.relay/config.toml` 의 `[lean] tools = "..."` 에 추가한다. 도구 하나를 더할 때마다 base 가 커진다는 점만 기억하면 된다.
- 프로젝트 `.mcp.json` 의 서버(예: relay MCP)만 켜고, 전역 MCP 서버는 끈다.

## 2. `relay run` — 작업 하나를 끝까지

```
explore (Gemini CLI) → pack (relay) → implement (Codex) → verify (테스트) → review (Claude lean)
                                          ↑──────────── VERDICT: CHANGES 면 리뷰 노트를 들고 재구현 ──┘
```

| 단계 | 러너 (기본, 앞에서부터 설치된 것) | Claude 토큰 | 산출물 |
|---|---|---|---|
| explore | `gemini -p` → `codex exec --sandbox read-only` | 0 | `explore.md` (stdout 을 relay 가 저장, 쓰기 권한 불필요) |
| pack | relay | 0 | `pack.md` |
| implement | `codex exec --full-auto` | 0 | 코드 변경, `out-codex.md` |
| verify | `pipeline.verify` 셸 명령 | 0 | `verify.md` (PASS/FAIL + 출력 꼬리 40줄) |
| review | `claude -p` lean (Read/Grep/Glob, 짧은 system prompt, 새 세션) | **여기만** | `out-claude.md` (`VERDICT: APPROVE / CHANGES`) |

```bash
cat >> .relay/config.toml <<'EOF'
[pipeline]
verify = "python -m pytest -q"
review_model = "sonnet"
review_effort = "medium"
EOF

relay task new "calc.add 버그 수정"     # brief.md 작성 (ChatGPT 로 다듬어도 됨)
relay run --dry-run                     # 어떤 러너가 쓰일지 확인
relay run                               # 끝까지 실행
relay run --skip explore pack           # 재구현 · 재리뷰만
relay run --only review                 # 직접 고친 뒤 리뷰만
```

### 실측 (Claude 리뷰 단계는 실제 실행, explore · implement 는 이 환경에 Gemini/Codex 가 없어 대역 스크립트)

| 실행 | Claude 사용 |
|---|---|
| 리뷰 1회 (CHANGES) | 1턴 · 컨텍스트 6.5k · 출력 524 |
| 리뷰 1회 (APPROVE) | 1턴 · 컨텍스트 5.5k · 출력 126 |

같은 수정을 기본 대화형 세션에서 하면 첫 메시지만으로 35k 이고, 파일 읽기 · 수정 · 테스트로 요청이 5~10번 이어지면 수십만 토큰이 된다.

### 설계 결정

- **Claude 는 판단만, 매번 새 세션.** 세션 길이의 이차 항이 생기지 않는다. 리뷰 노트(`out-claude.md`)는 Codex 에게 넘어가고 Claude 대화는 쌓이지 않는다.
- **리뷰 입력은 diff + 테스트 꼬리.** 저장소 전체가 아니라 바뀐 부분만. `.relay/` 는 diff 에서 제외, 새 파일은 포함하되 바이너리 · `__pycache__` 는 제외, `review_budget` (기본 15k)으로 상한.
- **러너는 설정.** `[runners]` 에서 후보 목록을 바꿀 수 있다. 설치되지 않은 러너는 다음 후보로, 모두 없으면 그 단계는 건너뛴다 (구현이 없으면 리뷰 전에 멈춘다).
- **모든 단계가 기록된다.** `.relay/tasks/<id>/run.json` (단계별 상태 · 시간 · Claude 사용량), `relay events`.

### 한계

- Gemini CLI · Codex CLI 러너는 이 환경에서 실행해 보지 못했다 (설치 불가). 플래그는 각 CLI 의 비대화형 모드 기준이며, 첫 실행 전에 `relay run --dry-run` 과 각 CLI 의 `--help` 로 확인한다. Gemini CLI 의 비대화형 모드에서 어떤 도구가 자동 허용되는지는 버전에 따라 다를 수 있다.
- Antigravity 는 비대화형 CLI 가 없어 파이프라인에 넣지 않았다. 대화형 탐색은 기존처럼 MCP(`explorer` 프로필)로 쓴다.
- ChatGPT 웹은 자동화할 수 없다. 같은 구독의 Codex CLI 가 GPT 쪽 자동화를 맡는다.
