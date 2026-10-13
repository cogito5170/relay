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
explore (agy) → pack (relay) → implement (Codex) → verify (테스트) → review (Claude lean)
                                          ↑──────────── VERDICT: CHANGES 면 리뷰 노트를 들고 재구현 ──┘
```

| 단계 | 러너 (기본, 앞에서부터 설치된 것) | Claude 토큰 | 산출물 |
|---|---|---|---|
| explore | `agy --mode plan --print` → `codex exec --sandbox read-only` | 0 | `explore.md` (stdout 을 relay 가 저장, 쓰기 권한 불필요) |
| pack | relay | 0 | `pack.md` |
| implement | `codex exec --sandbox workspace-write` | 0 | 코드 변경, `out-codex.md` |
| verify | `pipeline.verify` 셸 명령 | 0 | `verify.md` (PASS/FAIL + 출력 꼬리 40줄) |
| review | `claude -p` lean (Read/Grep/Glob, 짧은 system prompt, 새 세션) → `agy --model claude-opus-5-5-medium` | **여기만** (agy 폴백은 0) | `out-claude.md` (`VERDICT: APPROVE / CHANGES`) |

```bash
cat >> .relay/config.toml <<'EOF'
[pipeline]
verify = "python -m pytest -q"
review_model = "sonnet"
review_effort = "medium"
review_max_turns = 8          # 비용은 턴 수가 지배한다. 낮추면 VERDICT 가 잘릴 수 있다
EOF

relay task new "calc.add 버그 수정"     # brief.md 작성 (ChatGPT 로 다듬어도 됨)
relay run --dry-run                     # 어떤 러너가 쓰일지 확인
relay run                               # 끝까지 실행
relay run --skip explore pack           # 재구현 · 재리뷰만
relay run --only review                 # 직접 고친 뒤 리뷰만
relay run --review-max-turns 4 --review-model opus   # 이번 실행만 리뷰 설정 덮어쓰기
```

### 실측 1 — 대역 스크립트 (explore · implement 를 스텁으로, Claude 리뷰만 실제 실행)

| 실행 | Claude 사용 |
|---|---|
| 리뷰 1회 (CHANGES) | 1턴 · 컨텍스트 6.5k · 출력 524 |
| 리뷰 1회 (APPROVE) | 1턴 · 컨텍스트 5.5k · 출력 126 |

### 실측 2 — 실제 CLI 전부 (2026-10-13, gemini-cli 0.46.0 · codex-cli 0.152.0 · claude 리뷰 sonnet/medium)

이 저장소에서 작업 하나(`relay/config.py` 러너 기본값 수정 + 문서 + 테스트)를 끝까지 돌렸다.

| 실행 | 결과 | Claude 사용 (실제 토큰) |
|---|---|---|
| 1회차 (implement 가 빈손, 리뷰가 잡아냄) | CHANGES → 재구현 → CHANGES | 15턴 · 컨텍스트 66.3k · 출력 3.0k |
| 2회차 (implement 정상) | **APPROVE, 1 iteration** | 3턴 · 컨텍스트 35.8k · 출력 326 |

2회차 리뷰 1회의 내역: `cache_creation 9.0k` + `cache_read 26.8k` + `output 326`.
**가중 사용량 약 15.6k** (= 9.0k×1.25 + 26.8k×0.1 + 326×5).

- 턴 수가 비용을 지배한다. 턴마다 전체 컨텍스트가 다시 읽히므로 (cache_read) 3턴이면 고유 입력의 3배가 흐른다.
  리뷰가 파일을 열지 않고 1턴에 끝나면 위 실측 1처럼 5~7k 수준이다. diff 가 크거나 모호하면 Claude 가 파일을 열어 턴이 늘어난다.
- 따라서 **리뷰 비용을 줄이는 가장 큰 레버는 brief 와 diff 를 명확하게 만드는 것**이다 (Done when 을 검증 가능하게 쓰면 리뷰가 파일을 덜 연다).
- 같은 수정을 기본 대화형 세션에서 하면 첫 메시지만으로 35k 이고, 파일 읽기 · 수정 · 테스트로 요청이 5~10번 이어지면 수십만 토큰이 된다.

실제 실행에서 걸린 문제와 대응:

| 증상 | 원인 | 대응 |
|---|---|---|
| implement 가 "ok" 인데 변경 0 | codex 가 사이드카 `codex-code-mode-host` 를 argv0 디렉터리에서 찾는다. `codex` 만 심볼릭 링크하면 모든 파일·셸 도구가 죽는다 | 두 바이너리를 같은 디렉터리에 링크 (`codex`, `codex-code-mode-host`) |
| explore 가 76~235초 뒤 `An unexpected critical error occurred:[object Object]` | Gemini API 503 (과부하) 재시도 끝에 프로세스 전체가 죽음. **이 때문에 explore 1순위를 `gemini` 에서 `agy` 로 바꿨다** | `_agent` 가 다음 러너 후보(`codex exec --sandbox read-only`)로 자동 재시도 |
| `codex exec --full-auto` 가 exit 2 | codex-cli 0.152 에서 플래그 삭제 | 기본값을 `codex exec --sandbox workspace-write` 로 (exec 의 승인 모드는 기본이 never) |
| 리뷰가 "성공"했는데 VERDICT 가 없다 | 한도 소진 메시지 · `--max-turns` 로 잘린 응답도 종료 코드 0 이다 | `_agent(accept=...)` 가 결론 없는 출력을 실패로 보고 다음 후보(agy)로 넘긴다 |

### 실측 3 — 토큰·안정성 검증 (2026-10-13, 임시 저장소 `calc.add` 버그 1개)

| 항목 | 값 |
|---|---|
| 실제 Claude 리뷰 1회 (sonnet/medium, 1턴) | 컨텍스트 **9.8k** (`cache_creation 9,823`) · 출력 13 · **가중 12.3k** · 4.5초 |
| 문서 실측 2 (3턴) | 컨텍스트 35.8k · 가중 15.6k |
| 한 `relay run` 의 최악값 (기본값 `max_iterations 2` × `review_max_turns 8` = 16턴, 출력 1k/턴 가정) | **가중 약 107k = 1턴의 8.7배** |
| agy 폴백 리뷰 (claude-opus-5-5-medium, plan 모드) | Claude 토큰 **0** · 12~90초 · 5/5 회 VERDICT 산출 |

검증한 안정성 항목:

| 상황 | 재현 방법 | 결과 |
|---|---|---|
| Claude 한도 소진 | PATH 에 `claude` 셰임(종료 0, `"Claude AI usage limit reached"`, VERDICT 없음) | `[ retry ] review claude 실패: 결론 없는 출력 → agy 로 재시도` 후 agy 가 리뷰 완료. 한도 소진이 리뷰 누락으로 이어지지 않는다 |
| codex 조용한 실패 | PATH 에 `codex` 셰임(종료 0, 변경 0) | `implement failed 변경 0 — 러너가 파일을 고치지 못했습니다` 로 **리뷰 전에 중단**. 토큰 0 으로 발견 |
| 리뷰 입력 폭주 | 추적 안 되는 80.4k tok 새 파일 추가 | `pack` 이 예산(15k) 초과 섹션을 버려 리뷰 입력 1.3k 유지. 단 **버려진 새 파일은 리뷰어가 못 본다** |
| agy 승인 대기 교착 | plan 모드에서 셸 명령이 필요한 프롬프트 | `jetski: no output produced — a tool required the "command" ...` 로 빈 출력. 리뷰·탐색 프롬프트에 "셸 명령을 쓰지 말라"를 넣어 5/5 회로 해소. `--sandbox` (plan 없이) 면 명령이 자동 실행되지만 파일 수정도 가능해져 리뷰어에는 쓰지 않는다 |
| 같은 입력, 다른 판정 | 동일 diff 로 agy 리뷰 반복 | CHANGES → APPROVE 로 갈렸다. agy 폴백은 Claude 대체가 아니라 **한도 소진 시의 차선**으로 본다 |

남은 노출:

- 최악 가중 107k 는 상한일 뿐 하드 캡이 아니다. 누적 토큰으로 런을 끊는 장치는 없다 (`pipeline.run_token_budget` 같은 옵션이 필요하면 추가).
- agy 는 토큰 사용량을 출력하지 않아 `run.json` 의 `claude` 가 비고, 폴백으로 넘어간 런의 비용은 계측되지 않는다.

### 실측 4 — 모델별 한도 소진 대응 (2026-10-13)

agy 는 모델마다 쿼터가 따로다. 바이너리에 `You have exhausted your quota on this model.` 문구가 있고, **모델을 자동으로 바꿔 주지는 않는다** (그런 동작을 가리키는 문자열이 없다). 한도 소진은 claude 든 agy 든 **종료 코드 0 + 안내 문구**로 오므로, 종료 코드만 보면 "성공"으로 통과한다.

대응 두 겹:

1. `quota_exhausted()` 가 출력에서 한도 문구를 찾으면 그 후보를 실패로 보고 다음 후보로 넘긴다 (모든 단계 공통).
2. 단계마다 **쿼터가 다른 공급자**로 체인을 깐다. 모델 이름은 설정값이다.

| 단계 | 후보 (앞에서부터) | 쿼터 |
|---|---|---|
| explore | `agy --mode plan` → `codex exec --sandbox read-only` | Antigravity → OpenAI |
| implement | `codex exec --sandbox workspace-write` → `agy --sandbox --mode accept-edits --model {implement_fallback_model}` | OpenAI → Antigravity |
| review | `claude -p` → `agy --mode plan --model {review_fallback_model}` → `codex exec --sandbox read-only` | Anthropic → Antigravity → OpenAI |

셰임으로 한도 소진을 재현한 결과:

```
[ retry ] review    claude 실패: 한도 소진 ('usage limit reached') → agy 로 재시도
[ retry ] review    agy 실패: 한도 소진 ('exhausted your quota') → codex 로 재시도
[  ok   ] review      0.5s codex VERDICT APPROVE
```

```
[ retry ] implement codex 실패: 한도 소진 ('exhausted your quota') → agy 로 재시도
[  ok   ] implement  70.2s agy            # 실제로 calc.py 를 고쳤고 verify PASS
```

**agy 헤드리스의 함정:** 셸 명령 권한은 헤드리스에서 물을 수 없어 자동 거부되고, 그 run 은 출력 0 으로 끝난다 (`jetski: no output produced — a tool required the "command" permission ...`). 처음 agy implement 폴백이 변경 0 으로 실패한 원인이 이것이다. 그래서 agy 를 쓰는 후보에는 `NO_SHELL_NOTE` ("명령 대신 파일 도구만 써라, 테스트는 파이프라인이 돌린다") 를 프롬프트 앞에 붙인다. 붙인 뒤 implement 폴백이 성공했다.
명령을 꼭 쓰게 하려면 `~/.gemini/antigravity-cli/settings.json` 의 `permissions.allow` 에 `command(<target>)` 를 넣는다 (정확한 문자열 매칭이라 깨지기 쉽다). relay 는 이 파일을 건드리지 않는다.

### 감시자 (`relay watch`) — Claude 를 감시하는 모델

hook 의 차단은 숫자만 본다(컨텍스트 N, 요청 M). "지금 Claude 가 하는 일이 Claude 가 할 일인가" 는 판단이 필요하고, 그 판단을 Claude 에게 맡기면 감시가 비용이 된다. 그래서 **codex 1순위 · agy 폴백**으로 돌린다. `supervisor.assert_not_claude()` 가 러너에 claude 가 들어오면 거부한다.

```
relay watch                 # 판정 받고 .relay/supervisor/<session>.json 에 캐시 (세션별)
relay watch --snapshot      # 감시 모델에게 보낼 스냅샷만 (모델 호출 0)
relay watch --cached        # 캐시만 보기
relay hook prompt           # 캐시된 판정을 즉시 반영 (모델 호출 없음 = 0 토큰)
```

- 스냅샷은 전사 전체가 아니라 `relay why` 의 수치 + 가장 비싼 항목 5개 + 최근 도구 호출 8개다. **실측 292 토큰.**
- 판정 4단계: `CONTINUE`(그대로) · `DELEGATE`(다음 작업은 다른 도구로) · `HANDOFF`(파일로 넘기고 마무리) · `CLEAR`(지금 /clear).
- hook 은 **모델을 호출하지 않는다.** 캐시가 낡고(`max_age` 900초) 컨텍스트가 `soft_context`(40k) 를 넘으면 갱신만 백그라운드로 띄우고 이번 메시지는 통과시킨다.
- `CLEAR` 만 메시지를 막는다 (`[force]` 로 통과). `DELEGATE`·`HANDOFF` 는 `additionalContext` 로 한 줄 안내만 — 감시자가 작업을 임의로 끊지 않게 한다.
- 상태줄에도 `감시자 HANDOFF` 로 붙는다 (파일 읽기만).

실측 (2026-10-13, 이 저장소의 67요청 세션):

```
$ relay watch --refresh
VERDICT: HANDOFF  (codex, 11.5초, 스냅샷 ~292 tok)
이유: 67 requests caused 2,627,494 re-read tokens; context exceeds the 60,000 maximum.
조치: End Claude session 7fd51dd0 and hand remaining implementation to Codex using the relay state.
```

codex 가 11.5초에 판정했고 Claude 토큰은 0 이다. codex 가 한도 소진·실패면 `agy --mode plan` 으로 넘어간다 (`quota_exhausted` 와 "VERDICT 없음" 을 모두 실패로 본다).

### 설계 결정

- **Claude 는 판단만, 매번 새 세션.** 세션 길이의 이차 항이 생기지 않는다. 리뷰 노트(`out-claude.md`)는 Codex 에게 넘어가고 Claude 대화는 쌓이지 않는다.
- **리뷰 비용은 턴 수가 지배한다.** 턴마다 컨텍스트 전체가 다시 읽히므로, 리뷰 system prompt 에 "턴마다 전체 컨텍스트가 다시 과금되니 읽기는 한 턴에 모아라" 를 명시했다. 상한은 `review_max_turns` (기본 8, `relay run --review-max-turns` 로 덮어쓰기).
- **리뷰 입력은 diff + 테스트 꼬리.** 저장소 전체가 아니라 바뀐 부분만. `.relay/` 는 diff 에서 제외, 새 파일은 포함하되 바이너리 · `__pycache__` 는 제외, `review_budget` (기본 15k)으로 상한.
- **러너는 설정.** `[runners]` 에서 후보 목록을 바꿀 수 있다. 설치되지 않은 러너는 다음 후보로, 모두 없으면 그 단계는 건너뛴다 (구현이 없으면 리뷰 전에 멈춘다).
  **실행이 0 이 아닌 코드로 끝나도 다음 후보로 재시도한다** (503 같은 일시적 실패). 재시도는 로그에 `[ retry ]` 로 남는다.
- **모든 단계가 기록된다.** `.relay/tasks/<id>/run.json` (단계별 상태 · 시간 · Claude 사용량), `relay events`.

### 한계

- 러너 플래그는 CLI 버전마다 바뀐다 (`--full-auto` 는 codex-cli 0.152 에서 사라졌다). 새 환경에서는 첫 실행 전에 `relay run --dry-run` 과 각 CLI 의 `--help` 로 확인한다.
- 탐색 러너가 죽고 재시도 후보도 없으면 explore 가 비고, `pack.md` 가 0 섹션이 된다 (그래도 implement 는 brief 만으로 진행한다 — 위 실측 2 가 그 경우다).
- `agy` 는 `--print` 가 프롬프트를 바로 뒤 인자로 받는다. `--mode plan` 을 `--print` 뒤에 두면 모드 이름이 프롬프트로 먹힌다. 파일은 읽고 쓰기는 하지 않는 plan 모드로 돌린다.
- Gemini API 는 쓰지 않는다 (503 빈발). `gemini -p` 는 기본 후보에서 빠졌다. 필요하면 `[runners] explore` 에 다시 넣을 수 있다.
- Antigravity 는 `agy --print` 로 비대화형 실행이 되어 explore 1순위로 넣었다 (plan 모드, 2026-10-13 확인). 대화형 탐색은 기존처럼 MCP(`explorer` 프로필)로도 쓴다.
- ChatGPT 웹은 자동화할 수 없다. 같은 구독의 Codex CLI 가 GPT 쪽 자동화를 맡는다.
