# STATE — 2026-10-13 세션 기록

브랜치 `claude/zealous-goodall-wkb3yz`. 상세 배경은 `docs/HANDOFF.md`, 측정값은 `docs/pipeline.md` "실측 2".

## 한 일

`relay run` 을 실제 CLI 로 처음 끝까지 돌렸다 (gemini-cli 0.46.0, codex-cli 0.152.0, claude 리뷰 sonnet/medium). 작업 `001` 에서 codex 가 구현하고 Claude 가 APPROVE 했으며, 1회차에 codex 가 아무것도 고치지 못한 것을 리뷰가 잡아냈다.

실행에서 드러난 문제 세 가지를 고쳤다 (커밋 `5e43f3a`, `v0.4.1`).

1. `codex exec --full-auto` 는 codex-cli 0.152 에서 사라졌다. 기본값을 `codex exec --sandbox workspace-write` 로 바꿨다.
2. codex 는 사이드카 `codex-code-mode-host` 를 실행 파일과 같은 디렉터리에서 찾는다. `~/.local/bin` 에 두 바이너리를 모두 링크했다.
3. Gemini 가 503 으로 죽으면 explore 가 통째로 날아갔다. 러너가 0 이 아닌 코드로 끝나면 다음 후보로 재시도하도록 `installed_runners` 를 추가했다.

이어서 두 가지를 더 했다 (아직 커밋 전).

4. 리뷰 턴 상한을 설정으로 뺐다: `pipeline.review_max_turns` (기본 8), `relay run --review-max-turns` / `--review-model` 로 실행 단위 덮어쓰기. 리뷰 system prompt 에 "턴마다 컨텍스트 전체가 다시 과금되니 읽기는 한 턴에 모아라" 를 넣었다. `--dry-run` 은 채워진 값과 폴백 사슬을 보여준다.
5. `relay why` 의 미분류 몫을 분해했다. transcript 의 thinking 블록은 본문이 비어 있고(`signature` 만) `usage.thinking_tokens` 도 0 이라, 직전 응답의 `output_tokens` 에서 본문·tool_use 로 설명되는 몫을 뺀 나머지를 thinking 으로 돌린다. 이 저장소의 CLI 작업 세션(67요청, 재독 2.6M)에서 미분류 37% → thinking 18% + 포맷·주입 overhead 20% 로 나뉘었다.

변경 파일: `relay/attribution.py`, `relay/config.py`, `relay/pipeline.py`, `relay/cli.py`, `tests/test_pipeline.py`, `tests/test_diagnostics.py` (54개 통과), `docs/HANDOFF.md`, `docs/pipeline.md`, `docs/playbook.md`.

## 결정

- 리뷰 비용의 실측치는 가중 약 15.6k (컨텍스트 35.8k, 3턴) 이며, 기존 문서의 5.5~6.5k 는 1턴 대역 스크립트 기준이었다. 비용은 턴 수가 지배하므로 brief 와 diff 를 명확히 쓰는 쪽이 가장 큰 레버다.
- `--max-turns` 기본값은 8 로 둔다. 4 는 설정으로 쓸 수 있게만 해 두고, VERDICT 품질은 실제 작업으로 확인한다.
- 남은 overhead 20% 는 블록 포맷과 세션 중 주입되는 system-reminder 다. transcript 에 기록이 없어 더 쪼갤 수 없고, 줄이려면 요청 수 자체를 줄여야 한다.
- `relay init` 산출물(`.relay/`, `.claude/`, `.agent/`, `AGENTS.md`, `CLAUDE.md`)은 머신마다 달라 git 에서 제외한다.
- 이 저장소에는 가상환경이 없어 `.venv` 를 만들어 `pytest` 를 설치했다 (`.gitignore` 에 이미 있다). 실행은 `.venv/bin/python3 -m pytest tests/ -q`.

## 이어서 한 일 (같은 날)

6. 읽기 범위 상한을 가드에 넣었다 (커밋 `9cd9353`). `hooks.read_max_range` (기본 120줄) 를 넘는 Read `limit`, `sed -n 'A,Bp'`, `head -n N`, `tail -n N` 을 거부하고 "Grep 으로 위치를 찾고 120줄 이하로 좁혀서 읽으세요" 로 안내한다. 범위 플래그가 있으면 `LIMITERS` 가 통과시키므로 `range_guard` 는 그 검사보다 먼저 돈다. `relay why` 에서 `sed -n '120,320p'` 한 번이 143k (5.4%) 였던 게 계기다.
7. **Gemini API 를 쓰지 않기로 했다.** explore 1순위를 `agy --mode plan --print` (Antigravity CLI) 로 바꾸고, 실패하면 `codex exec --sandbox read-only` 로 넘어간다. `agy --print` 는 프롬프트를 바로 뒤 인자로 받으므로 `--mode plan` 을 앞에 둬야 한다 (뒤에 두면 모드 이름이 프롬프트로 먹힌다). plan 모드에서 파일 읽기가 비대화형으로 동작하는 것을 확인했다 (rc=0).

8. 리뷰 폴백과 implement 공회전 감지를 넣고 토큰·안정성을 실측했다 (`docs/pipeline.md` "실측 3").
   - 리뷰 후보에 `agy --mode plan --model {review_fallback_model}` (기본 `claude-opus-5-5-medium`) 를 추가했다. agy 에는 `--system-prompt` 가 없어 프롬프트 앞에 붙인다.
   - `_agent(accept=...)`: 종료 코드 0 이어도 쓸 수 없는 출력이면 다음 후보로 넘긴다. 리뷰는 "VERDICT 없음"을 실패로 본다 (한도 소진 메시지·턴 잘림이 전부 종료 0 이다).
   - `implement` 는 실행 전후 `workspace_fingerprint` 를 비교해 변경 0 이면 실패로 만든다. codex 사이드카 누락 같은 조용한 실패를 리뷰 토큰 쓰기 전에 잡는다.
   - `verdict_of` 는 `**VERDICT: APPROVE**` 처럼 꾸민 줄도 받아들인다.
   - plan 모드 agy 가 셸 명령을 쓰려 하면 승인 대기로 빈 출력이 된다. 리뷰·탐색 프롬프트에 "셸 명령 금지" 를 넣어 5/5 회 VERDICT 산출로 해소했다.

9. 모델별 한도 소진 대응을 넣었다 (`docs/pipeline.md` "실측 4"). agy 는 모델마다 쿼터가 따로이고 자동 전환이 없다.
   - `quota_exhausted()`: 종료 코드 0 이어도 출력에 한도 문구(`exhausted your quota`, `usage limit reached` 등)가 있으면 실패로 보고 다음 후보로 넘긴다.
   - 단계마다 공급자가 다른 체인: explore `agy → codex`, implement `codex → agy(accept-edits)`, review `claude → agy → codex`. 폴백 모델은 `pipeline.implement_fallback_model` · `review_fallback_model`.
   - agy 헤드리스는 셸 명령 권한을 자동 거부하고 출력 0 으로 끝난다. agy 후보 프롬프트에 `NO_SHELL_NOTE` 를 붙여 해결했다 (붙이기 전 implement 폴백은 변경 0 실패, 붙인 뒤 70.2초에 성공 + verify PASS).

## 다음 단계

1. `--review-max-turns 4` 로 실제 작업을 리뷰해 VERDICT 가 잘리지 않는지 확인 (잘리면 6 으로). 참고: 실측 3 의 리뷰는 1턴 · 가중 12.3k 였고, 최악값은 16턴 · 가중 107k 다.
2. implement 가 agy 로 넘어간 런은 `--sandbox` 안에서 돌아 셸 명령이 거부된다. 테스트까지 러너가 돌려야 하는 작업이면 `permissions.allow` 설정이 필요하다 — 필요해지면 `relay doctor` 에 점검 항목으로 넣을지 결정.
3. 누적 토큰 하드 캡(`pipeline.run_token_budget`) 을 넣을지 결정. 지금은 턴·반복 상한만 있고 누적으로 끊지 않는다.
4. pack 이 예산 초과로 **버린 새 파일을 리뷰어가 못 보는** 문제: 최소한 "버린 파일 목록" 을 리뷰 입력에 남길지 결정.
5. `relay run` 을 agy explore 로 한 번 끝까지 돌려 `explore.md` 품질 확인.
6. `relay lean` 상시 사용, hooks 를 Sonnet/Opus 세션에서 확인.
7. 남은 가드 구멍: `Read(offset만, limit 없음)` · `awk 'NR>=A&&NR<=B'` · `grep -A/-B/-C N` 은 아직 통과한다. hooks 는 `effiency/.claude/settings.local.json` 에만 있어 `/Users/cogito` 에서 시작한 세션에는 적용되지 않는다 (전역 `~/.claude/settings.json` 설치 검토).
