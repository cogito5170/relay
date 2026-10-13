# HANDOFF — relay 프로젝트 인수인계

> 다음 Claude 세션은 **이 파일만 먼저 읽고** 시작한다. 저장소 전체를 다시 훑지 말 것.
> 필요한 파일은 아래 "파일 지도"에서 골라 필요한 범위만 읽는다.

## 목표

**Claude Pro 요금제로 30일간 한도 초과 없이 안정적으로 운용한다.** (Max 5x → Pro, 사용 가능량 약 1/5)
모든 토큰 판단은 Pro 기준이다. Claude 는 판단·리뷰에만 쓰고, 탐색·구현·문서는 Gemini / Codex / ChatGPT 로 넘긴다.

## 현재 상태 (브랜치 `claude/zealous-goodall-wkb3yz`, v0.4.0, CI 통과)

| 명령 | 역할 | 검증 |
|---|---|---|
| `relay lean` | 도구 6개만 켠 대화형 Claude | 실측 |
| `relay run` | explore(Gemini) → pack → implement(Codex) → verify → review(Claude lean), CHANGES 면 재구현 | Claude 리뷰는 실측, Gemini/Codex 는 대역 스크립트로만 |
| `relay init --hooks` | 컨텍스트 한도 넘으면 메시지 차단, 큰 파일 통째 Read · 무제한 출력 명령 차단 | 실제 세션 실측 |
| `relay why` / `relay doctor` | 캐시 읽기 원인 분해 / 고정 부분 진단 | 실측 |
| `relay usage --save-baseline` | 기준선 저장, 이후 배수 비교 | 실측 |
| `relay task` · `pack` · `handoff` · `mcp` | 파일 기반 인수인계, MCP 서버(Claude Code 연결 확인) | 테스트 |

## 핵심 측정값 (Claude Code 2.1.296, `claude -p`, Haiku)

- 첫 요청 컨텍스트(고정 부분): 기본 **35.1k** → `--tools` 6개 **7.9k (−78%)** → 리뷰용 3개 + 짧은 system prompt **4.0k (−89%)**. 고정 부분의 대부분은 **기본 도구 설명**이다.
- `relay run` 리뷰 1회: **1턴, 컨텍스트 5.5~6.5k**. 기본 대화형은 첫 메시지만 35k.
- 긴 세션(이 대화) 분해: 고정 48% · 미분류 19% · thinking 13% · Claude 가 쓴 파일 13% · 명령 출력 3%.
- hook 으로 차단된 메시지는 **모델 호출 0회 · 토큰 0**.

## 결정과 이유

- **RAG 보류**: 탐색은 이미 Gemini → pack 으로 Claude 토큰 0. 측정상 큰 비용(고정 부분 · 세션 길이 · thinking)을 RAG 는 못 줄인다.
- **Claude 는 매번 새 세션**: 대화가 길어지면 요청마다 전체를 다시 읽는다(요청 수에 대해 이차로 증가).
- **`--bare` 미사용**: API 키 인증만 허용해서 구독 사용자는 못 쓴다.
- **hooks 는 fail-open, MCP 쓰기 권한은 fail-closed**: 생산성 장치는 오류 시 통과, 권한 장치는 오류 시 차단.
- **ChatGPT 웹 · Antigravity 는 파이프라인 밖**: 비대화형 CLI 가 없다. GPT 쪽 자동화는 Codex CLI, Antigravity 는 MCP `explorer` 프로필로 대화형 탐색.

## 30일 운용 계획

Pro 한도는 5시간 단위 사용량과 주간 사용량으로 관리된다. 정확한 토큰 수치는 공개되어 있지 않으므로 **Claude 앱 / Claude Code `/usage` 에 표시되는 % 를 기준**으로 삼는다.

**하루 예산 = 주간 한도의 약 12%** (1/7 = 14% 에서 여유분 약 15% 를 남김). 5시간 단위 한도도 한 번에 몰아 쓰지 않는다.

| 시기 | 할 일 |
|---|---|
| 전환 전 (Max 마지막 주) | `relay usage --days 7 --save-baseline` · 주 사용 프로젝트에 `relay init --hooks --mcp` · `relay doctor` 로 고정 부분 정리 · Codex/Gemini CLI 설치 후 `relay run` 1회 실전 |
| 1주차 | 매일 `/usage` % 와 `relay usage --days 1` 기록. 하루 12% 를 넘는 날은 `relay why` 로 원인 확인 |
| 2~4주차 | 주 1회 `relay usage --days 7` (기준선 대비 weighted x5 이상이 목표). 무거운 작업 유형은 라우팅 규칙에 추가 |

**사용 규칙**
1. 대화형은 항상 `relay lean`. 세션 하나 = 작업 하나, 끝나면 `/handoff` → `/clear`.
2. 할 일이 정해진 작업은 `relay run`. 리뷰 모델은 기본 Sonnet · effort medium, 사소한 변경은 Haiku, Opus 는 어려운 설계 결정에만.
3. 탐색은 Antigravity / Gemini, 구현 · 테스트 작성은 Codex, 조사 · 문서는 ChatGPT.
4. 큰 문서나 반복 코드를 Claude 가 직접 쓰지 않는다 (`write_guard` 켜기 권장).

**경보 단계** (주간 % 기준)
- 주 중반(4일차)에 60% 초과 → 리뷰를 Haiku 로, `review_effort = "low"`, 대화형 Claude 사용 중단하고 `relay run` 만.
- 85% 초과 → 남은 기간 Claude 는 최종 리뷰만. 나머지는 Codex / Gemini / ChatGPT.
- 한도 도달 → Codex · Gemini 로 계속 진행하고 리뷰는 다음 주기로 미룬다.

**Pro 설정** — v0.4.1 부터 기본값이다 (`relay init` 이 만드는 config.toml 포함). 기존 프로젝트는 `rm .relay/config.toml && relay init` 으로 갱신. 아래 `[pipeline] verify` 만 프로젝트에 맞게 직접 넣는다.
```toml
[budget]
warn_context = 40000
max_context = 60000
warn_requests = 30

[hooks]
enabled = ["prompt_gate", "read_guard", "bash_guard", "write_guard"]
gate_context = 60000
gate_requests = 50

[pipeline]
verify = "python -m pytest -q"   # 프로젝트에 맞게
review_model = "sonnet"
review_effort = "medium"
```

## 다음 할 일 (로컬 세션)

1. **Codex CLI · Gemini CLI 를 설치하고 `relay run --dry-run` → 실제 1회 실행.** 이 환경에서는 설치할 수 없어 실제로 돌려 보지 못했다. 플래그가 다르면 `relay/config.py` 의 `runners` 기본값을 고친다 (`gemini -p`, `codex exec --full-auto`, `codex exec --sandbox read-only`).
2. **`relay lean` 으로 평소 작업을 해 보고** 빠져서 불편한 도구가 있으면 `[lean] tools` 에 추가 (도구 하나 = 고정 부분 증가).
3. hooks 를 Sonnet / Opus 세션에서도 확인 (Haiku 에서만 확인했다).
4. `relay why` 의 미분류 19% 분석: 세션 중 주입되는 시스템 메시지 · 도구 정보로 추정, 확인 안 됨.

## 파일 지도

| 경로 | 내용 |
|---|---|
| `relay/pipeline.py` | `relay run`, `relay lean` |
| `relay/hooks.py` | Claude Code hooks |
| `relay/attribution.py` · `relay/doctor.py` | `relay why` · `relay doctor` |
| `relay/mcp_server.py` | MCP 서버 |
| `relay/config.py` | 모든 기본값 (`DEFAULTS`) |
| `docs/pipeline.md` · `docs/playbook.md` | 파이프라인 측정과 사용법 · 일상 운용법 |
| `tests/` | `python -m unittest discover -s tests` (47개, 외부 의존성 없음) |
