# 아키텍처 — 왜 이렇게 설계했나

## 1. Claude Code 의 토큰은 어디서 나가나

Claude Code 에서 요청 1번은 다음을 모두 모델에 다시 보낸다.

```
[시스템 프롬프트] [도구 정의 + MCP 도구] [CLAUDE.md] [지금까지의 대화 + 모든 tool 결과] [새 입력]
 └──────────────── base (고정) ────────────────┘ └───────── history (계속 증가) ─────────┘
```

이전 요청과 같은 앞부분은 prompt cache 에서 읽혀 `cache_read_input_tokens` 로, 새로 붙은 부분은 `cache_creation_input_tokens` 로 기록된다.
사람이 메시지를 한 번 보내도 Claude 가 파일을 5개 읽고 테스트를 2번 돌리면 **요청은 8번**이고, 매번 전체 컨텍스트를 다시 읽는다.

### 비용 모델

요청 i 의 컨텍스트를 `base + growth·i` 로 근사하면, 한 세션의 총 재독량은

```
cost(N) = N·base + growth·N(N−1)/2
```

| 시나리오 (base 20k, growth 2k) | 계산 | 재독량 |
|---|---|---|
| 세션 1개, 요청 100 | 100·20k + 2k·4950 | **11.9M** |
| 세션 4개 × 요청 25 | 4 × (25·20k + 2k·300) | 4.4M (x2.7) |
| 오프로드 50% → 세션 2개 × 요청 25 | 2 × (25·20k + 2k·300) | 2.2M (**x5.4**) |
| + base 를 12k 로 (MCP·CLAUDE.md 정리) | 2 × (25·12k + 2k·300) | 1.8M (x6.6) |

`relay estimate` 가 이 계산을 한다. 실제 base·growth 는 `relay usage` 의 `avg ctx` 와 세션별 최대 컨텍스트로 추정할 수 있다.

### cache read 를 줄이는 방법 = 위 식의 각 항을 줄이는 것

| 항 | 줄이는 방법 | 담당 |
|---|---|---|
| N (요청 수) | 탐색·구현을 다른 도구로. 독립적인 읽기는 한 응답에 병렬로 묶기 | router, CLAUDE.md 규칙 |
| N² (세션 길이) | 작업 단위마다 `/handoff` → `/clear` → `/pickup` | slash command, statusline |
| growth | 파일 전체 대신 라인 범위만 읽기, 로그는 `tail` 로 잘라서, pack 사용 | pack, CLAUDE.md 규칙 |
| base | 쓰지 않는 MCP 서버 끄기, CLAUDE.md 짧게, 서브 디렉터리 CLAUDE.md 정리 | playbook 체크리스트 |

## 2. 왜 "파일 기반 인수인계"인가

| 대안 | 문제 |
|---|---|
| 한 세션에서 계속 대화 | N² 항 폭증. `/compact` 도 결국 전체를 한 번 읽고 요약을 생성하는 비용이 든다 |
| 도구 간 대화 복붙 | 대화 로그는 장황하고 구조가 없어 받는 쪽 컨텍스트만 키운다 |
| API 로 직접 오케스트레이션 | 구독(ChatGPT Plus, Claude Pro, Antigravity)은 API 크레딧이 아니다. 구독을 최대한 쓰는 것이 목표 |
| **구조화된 파일 (채택)** | 모든 도구가 파일을 읽을 수 있다. 사람이 검토·수정할 수 있다. git 으로 이력이 남는다 |

`state.md` 는 **사람이 통제하는 수동 compact**다. 250단어 제한 덕에 다음 세션의 시작 컨텍스트가 작고, 같은 파일을 Codex 나 Antigravity 에 넘길 수도 있다.

## 3. pack 설계

Claude 가 스스로 탐색하면 `Glob → Grep → Read → Read → …` 로 요청이 늘고, 읽은 파일 전체가 history 에 쌓여 이후 모든 요청에서 다시 읽힌다.
pack 은 그 탐색을 **다른 도구(Antigravity)의 컨텍스트에서** 끝낸 뒤 결과만 가져온다.

1. **refs** — `explore.md` 에서 `path:start-end` 를 정규식으로 추출하고, 가까운 구간(8줄 이내)은 합친다. ±3줄 여유를 두고 라인 번호를 붙여 발췌한다.
2. **full** — 파일 전체. 예산을 넘으면 **outline 으로 강등**한다.
3. **outline** — Python 은 `ast` 로 클래스·함수 시그니처와 docstring 첫 줄만, 그 외 언어는 선언 줄만 정규식으로 뽑는다. 라인 번호를 붙여 Claude 가 필요할 때 범위를 좁혀 읽을 수 있게 한다.
4. **diff** — 리뷰 단계에서는 저장소 전체가 아니라 `git diff` 만 넘긴다.

예산(`pack_tokens`, 기본 12k)은 토크나이저 없이 근사한다 (ASCII 4자 ≈ 1토큰, 한글 1자 ≈ 1토큰). 목적은 정확한 과금이 아니라 "예산 안에 들어가는가"의 판단이다.

## 4. 관측성

- **Claude Code**: `~/.claude/projects/<project>/<session>.jsonl` 의 assistant 메시지 `usage`. 스트리밍 때문에 같은 응답이 여러 줄 기록되므로 `(message.id, requestId)` 로 중복을 제거한다. 서브에이전트 transcript (`<session>/subagents/*.jsonl`) 도 같은 한도를 쓰므로 합산한다.
- **Codex CLI**: `~/.codex/sessions/**/rollout-*.jsonl` 의 `token_count` 이벤트 누적값. `input_tokens` 에 `cached_input_tokens` 가 포함되어 있어 분리한다.
- **Antigravity / ChatGPT 웹**: 로컬 로그가 없어 측정하지 않는다 (해당 도구의 사용량 화면으로 확인).

### 가중치에 대한 주의

Anthropic 은 구독 한도가 cache read 를 정확히 어떤 비율로 계산하는지 공개하지 않는다. 그래서 relay 는 두 값을 함께 보여 준다.

- **raw**: 모든 토큰의 단순 합. cache read 가 압도적이다.
- **weighted**: API 가격 비율(input 1, cache write 1.25, cache read 0.1, output 5)을 근사치로 쓴 값. `config.toml` 에서 바꿀 수 있다.

최종 판단 기준은 Claude 앱·Claude Code 가 보여 주는 플랜 사용량(%)이다. relay 의 기준선 비교는 "어느 쪽으로 움직이고 있는가"를 보는 용도다.

## 5. 알려진 한계

- 라우터는 키워드 규칙이다. 오분류는 사람이 `--to` 로 바로잡는다. (v0.5 에서 실제 소요 토큰 기반으로 개선 예정)
- 도구 간 이동은 아직 복붙(`--copy`)이다. v0.2 의 MCP 서버가 이것을 대체한다.
- Antigravity 의 규칙/워크플로우 경로(`.agent/rules`, `.agent/workflows`)와 모델·쿼터 정책은 버전에 따라 바뀔 수 있다. 다르면 경로만 옮기면 된다.
- 다른 도구로 넘긴 만큼 품질 검증 책임이 생긴다. 그래서 파이프라인의 마지막 단계(리뷰)는 Claude 에 남겨 두었다.
