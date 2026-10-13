# 운영 플레이북 — 하루 사용법

## 0. 전환 전 (Max 플랜 마지막 주)

1. 평소처럼 일하면서 `relay usage --days 7 --save-baseline` 으로 **기준선**을 저장한다. 이게 없으면 "5배"를 확인할 방법이 없다.
2. `relay usage --sessions 10` 으로 cache read 상위 세션을 본다. 대부분 **요청 수가 많고 오래 이어진 세션**이다. 그 세션에서 Claude 가 한 일 중 탐색·반복 구현이 얼마나 되는지 확인한다. 그만큼이 오프로드 대상이다.
3. `relay why` 로 무거운 세션 하나를 분해한다. 어떤 원인이 큰지에 따라 처방이 다르다:

   | `relay why` 에서 큰 항목 | 처방 |
   |---|---|
   | base | `relay doctor` → MCP 서버 끄기, CLAUDE.md 줄이기 (효과가 가장 큼: 매 요청에 적용) |
   | thinking | 루틴 작업은 effort/thinking 을 낮추고, 어려운 판단에만 높이기 |
   | Write/Edit input | 큰 문서·반복 코드는 Codex/Antigravity 가 쓰고 Claude 는 diff 리뷰만 (`write_guard` 켜기) |
   | Bash / Read output | `relay init --hooks` (bash_guard · read_guard). 넓은 범위 읽기(`read_max_range`, 기본 120줄)도 함께 막힌다 |
   | 세션이 길다 (req 많음) | hooks 의 prompt_gate 가 한도에서 메시지를 막고 /handoff 를 안내 |
   | overhead (포맷·주입) | 더 쪼갤 수 없는 몫 (블록 포맷 · 세션 중 주입되는 system-reminder · 추정 오차). 줄이려면 요청 수 자체를 줄인다 |

4. `relay init --hooks` 로 규칙을 강제한다. CLAUDE.md 규칙은 모델이 잊을 수 있지만 hook 은 항상 실행된다. 막힌 횟수와 막은 크기는 `relay events` 의 avoided 로 본다.
   실제 Claude Code 세션(`claude -p`, 2.1.296)에서 확인한 동작:

   | 상황 | 결과 |
   |---|---|
   | 1000줄 파일을 범위 없이 Read | 차단 → Claude 가 Grep 으로 전환해 정답 도출, 2.7k tok 파일은 컨텍스트에 안 들어감 |
   | `pytest` 를 출력 제한 없이 실행 | 차단, 사유가 Claude 에게 전달됨 |
   | 컨텍스트 한도 초과 상태에서 메시지 전송 | 차단, **모델 호출 0회 · 토큰 0** (메시지가 모델에 가지 않음) |
   | 같은 메시지에 `[force]` | 정상 처리 |

5. 아래 "Claude Code 다이어트"를 적용하고 `relay doctor` 로 base 가 얼마나 줄었는지 확인한다.

## 1. 기본값 두 가지

- 대화형 Claude 는 항상 `relay lean` 으로 연다 (base 35.1k → 7.9k).
- 정해진 작업(버그 수정, 기능 추가)은 `relay run` 으로 돌린다. Claude 는 리뷰 1~2턴만 쓴다 ([pipeline.md](pipeline.md)).

아래 수동 경로는 파이프라인이 맞지 않는 작업(설계 논의, 원인 불명 디버깅)용이다.

## 1-1. 작업 하나의 표준 경로 (수동)

```
① ChatGPT    아이디어 → brief          relay task new "…" ; relay handoff N --to chatgpt --copy
② Antigravity 탐색 → explore.md        relay handoff N --to antigravity --copy
③ relay      pack 생성                 relay pack N --refs explore.md [--outline 'src/**/*.py']
④ Codex      구현 + 테스트              relay handoff N --to codex ; codex "$(cat …/prompt-codex.md)"
⑤ relay      diff pack                 relay pack N --refs explore.md --diff main
⑥ Claude     판단 · 리뷰 (새 세션)       relay handoff N --to claude ; claude "$(cat …/prompt-claude.md)"
⑦ Claude     작업 단위 끝                /handoff N → /clear → (이어서) /pickup N
```

MCP 를 연결했다면 ②④⑥ 의 `handoff`/복붙은 "003 탐색해줘", "003 구현해줘", `/pickup 003` 한 줄로 바뀐다 ([mcp.md](mcp.md)). ChatGPT 웹만 복붙이 남는다.

모든 작업이 7단계를 다 거칠 필요는 없다. `relay route "<설명>"` 의 추천을 출발점으로 쓴다.

| 작업 예시 | 경로 |
|---|---|
| "이 저장소 구조 파악" | ② 만 |
| "README / 설계 문서 작성" | ① 만 |
| "CRUD API + 테스트" | ① → ④ (Claude 생략) |
| "간헐적으로 실패하는 테스트 원인 찾기" | ② → ③ → ⑥ |
| "인증 구조 변경" | ① → ② → ③ → ⑥(설계) → ④(구현) → ⑤ → ⑥(리뷰) |

## 2. Claude 세션 규칙

- **세션 = 작업 단위 하나.** statusline 에 `⛔ /handoff → /clear` 가 뜨면 끊는다. 기본값은 컨텍스트 100k 또는 요청 40회.
- **첫 메시지에 pack 을 넣는다.** Claude 가 스스로 탐색하게 두지 않는다.
- **넓은 탐색이 필요하다고 하면** (CLAUDE.md 규칙에 따라 "Antigravity 탐색 필요: …" 라고 말함) 그 질문을 Antigravity 로 보내고, 결과를 `relay pack --refs` 로 다시 가져온다.
- **오래 자리를 비우기 전에 `/handoff`.** prompt cache 는 일정 시간(TTL)이 지나면 만료되고, 돌아와서 보내는 첫 요청이 컨텍스트 전체를 다시 cache write 한다 (cache read 보다 훨씬 비싸게 계산됨).
- **`/compact` 보다 `/handoff` + `/clear`.** compact 도 전체를 한 번 읽어야 하고, 요약 내용을 통제할 수 없다.
- **긴 출력을 붙여넣지 않는다.** 로그와 테스트 출력은 `| tail -50`, `grep` 으로 잘라서. 한 번 들어간 텍스트는 이후 모든 요청에서 다시 읽힌다.
- **루틴 작업에는 가벼운 모델·낮은 effort.** 출력 토큰은 입력보다 비싸게 계산된다.

## 3. Claude Code 다이어트 (base 줄이기)

| 항목 | 확인 방법 | 조치 |
|---|---|---|
| MCP 서버 | `/mcp`, `/context` | 이 프로젝트에서 안 쓰는 서버는 끈다. 도구 정의는 매 요청 base 에 들어간다 |
| CLAUDE.md | 전역 `~/.claude/CLAUDE.md` + 프로젝트 + 하위 디렉터리 | 짧게. 자세한 문서는 `@import` 대신 필요할 때 읽게 링크만 |
| relay MCP | `relay mcp --cost` | Claude 에는 `claude` 프로필(약 544 tok)만. `full` 은 쓰지 않는다 |
| 커스텀 에이전트 / 스킬 | 설명 문구가 base 에 들어감 | 안 쓰는 것은 제거 |
| 서브에이전트 | 별도 컨텍스트지만 같은 한도를 씀 | 탐색 목적이면 Antigravity 로 |

`/context` 로 무엇이 컨텍스트를 차지하는지 바로 볼 수 있고, `relay usage` 의 세션별 첫 요청 크기로 효과를 확인할 수 있다.

## 4. 도구별 팁

**ChatGPT** — 저장소를 못 보므로 `relay handoff --to chatgpt` 프롬프트에 brief · state · pack 이 모두 들어간다. 답변은 그대로 `brief.md` 나 `out-chatgpt.md` 에 저장한다. 긴 리서치나 여러 안 비교는 전부 여기서 한다.

**Antigravity** — `relay init` 이 `.agent/rules/relay.md` 와 `/relay-explore` 워크플로우를 설치한다. 탐색 결과는 반드시 `path:start-end` 목록으로 받는다 (pack 의 입력). Antigravity 에서 Claude 계열 모델을 고를 수 있는 시기라면, 그 사용량은 Anthropic 구독 한도와 별개다. 현재 모델 목록과 쿼터는 직접 확인한다.

**Codex CLI** — 저장소의 `AGENTS.md` 를 자동으로 읽는다. 구현이 끝나면 테스트를 돌리고 `out-codex.md` 를 쓰게 되어 있다. `relay usage --source codex` 로 Codex 쪽 사용량도 같이 본다.

## 5. 주간 점검 (5분)

```bash
relay usage --days 7            # 기준선 대비 배수: weighted x5.0 이상이 목표
relay usage --days 7 --sessions 5   # 여전히 무거운 세션이 있다면 → 그 작업 유형을 라우팅 규칙에 추가
```

배수가 목표에 못 미치면 순서대로 의심한다: ① 세션이 길어졌나 (req, avg ctx) ② Claude 가 탐색을 직접 했나 (pack 없이 시작) ③ base 가 다시 커졌나 (MCP 추가).
