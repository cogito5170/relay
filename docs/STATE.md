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

10. **Claude 감시자를 세웠다** (`relay/supervisor.py`, `relay watch`). 지금까지 Claude 를 감시하는 모델이 없었고 hook 의 숫자 한도만 있었다.
    - 판정은 codex(1순위) → agy(폴백). `assert_not_claude` 로 러너에 claude 를 넣지 못하게 막았다 — 감시가 Claude 쿼터를 쓰면 의미가 없다.
    - 스냅샷은 `relay why` 수치 + 비싼 항목 5개 + 최근 도구 8개로 **292 토큰**. 실측: codex 가 11.5초에 `HANDOFF` 판정, 이유에 실제 숫자(2,627,494 재독)를 인용했다.
    - hook 은 캐시된 판정만 읽어 0 토큰. `CLEAR` 는 차단(`[force]` 로 통과), `DELEGATE`·`HANDOFF` 는 안내 한 줄. 캐시가 낡고 컨텍스트가 40k 를 넘으면 갱신을 백그라운드로 띄우고 메시지는 통과시킨다.
    - 상태줄에 `감시자 <판정>` 표시. 설정은 `[supervisor]` (enabled, soft_context, max_age, timeout, runners, fallback_model).

11. **차단될 때 넘길 프롬프트가 없던 문제를 고쳤다** (hook 이 걸리면 `prompt_gate` 는 메시지를 모델에 보내지 않으므로, 그 시점에 상태가 안 남아 있으면 다음 세션이 이어받을 것이 없었다).
    - `/handoff` · `/pickup` 이 `effiency/.claude/commands/` 에만 있어 홈 디렉터리 세션에서는 존재하지 않았다. `~/.claude/commands/` 에 복사했고, 두 명령이 task 폴더가 없으면 `.relay/HANDOFF-NEXT.md` 로 떨어지도록 고쳤다.
    - `auto_wrapup` 을 `relay auto` 전용에서 대화형 세션까지 확장했다. 숫자 차단 직전(`hooks.wrapup_context`, 0 이면 `gate_context` 의 85%)에서 상태 파일 쓰기 외 모든 도구를 막아 인수인계를 받아낸다. 음수면 끈다.
    - 대화형 마무리는 `.relay/current` 나 `RELAY_TASK` 로 **지금 이 작업이라고 표시된** task 만 건드린다. `auto.current_task` 의 "가장 최근 task" 폴백을 그대로 쓰면 관계없는 옛 task 의 `state.md` 를 덮어쓴다 (실제로 이 세션에서 `003-platform-devops-final-16week-plan` 을 지목했다). 표시가 없으면 `.relay/HANDOFF-NEXT.md` 로 쓴다.
    - 차단 메시지(`prompt_gate` · 감시자 `CLEAR`)에 상태 형식과 쓸 파일 경로를 직접 넣었다. 전에는 `/handoff <id>` 만 안내해, task 가 없는 세션에서는 실행 불가능한 안내였다.
    - `session_start` 는 마무리가 쓰는 곳과 같은 기준(`_marked_task`)으로 읽는다: 표시된 task 의 `brief.md`+`state.md`, 표시가 없으면 `.relay/HANDOFF-NEXT.md`, 그 파일도 없으면 아무것도 주입하지 않는다 (2026-10-22 변경 — 전에는 `auto.current_task` 를 써서 표시 없는 저장소에 '가장 최근 task' 가 들어왔다). 2026-10-22 에 `~/.claude/settings.json` 에 `SessionStart`(`relay hook session`) 를 등록해, 홈 세션도 `/clear` · 새 세션 · 압축 직후 이 파일을 자동으로 받는다 (`/pickup` 불필요). `source=resume` 은 `session_inject` 에 없어 주입하지 않는다.
    - 첫 임계값을 `budget.max_context`(effiency 60k) 로 뒀더니 작업 중인 세션의 Bash·Read 를 61.4k 에서 바로 막았다. `budget.max_context` 는 상태줄 경고용이라 대화형 세션이 정상적으로 넘기는 값이어서 기준으로 쓸 수 없다. 테스트 116개 통과.

## 다음 단계

1. 감시자를 실사용해 보고 판정 품질 확인. codex 가 CONTINUE 를 너무 자주 주면 프롬프트에 임계 수치를 명시한다.
2. `--review-max-turns 4` 로 실제 작업을 리뷰해 VERDICT 가 잘리지 않는지 확인 (잘리면 6 으로). 참고: 실측 3 의 리뷰는 1턴 · 가중 12.3k 였고, 최악값은 16턴 · 가중 107k 다.
3. implement 가 agy 로 넘어간 런은 `--sandbox` 안에서 돌아 셸 명령이 거부된다. 테스트까지 러너가 돌려야 하는 작업이면 `permissions.allow` 설정이 필요하다 — 필요해지면 `relay doctor` 에 점검 항목으로 넣을지 결정.
4. 누적 토큰 하드 캡(`pipeline.run_token_budget`) 을 넣을지 결정. 지금은 턴·반복 상한만 있고 누적으로 끊지 않는다.
5. pack 이 예산 초과로 **버린 새 파일을 리뷰어가 못 보는** 문제: 최소한 "버린 파일 목록" 을 리뷰 입력에 남길지 결정.
6. `relay run` 을 agy explore 로 한 번 끝까지 돌려 `explore.md` 품질 확인.
7. `relay lean` 상시 사용, hooks 를 Sonnet/Opus 세션에서 확인.
8. 남은 가드 구멍: `Read(offset만, limit 없음)` · `awk 'NR>=A&&NR<=B'` · `grep -A/-B/-C N` 은 아직 통과한다. hooks 는 `effiency/.claude/settings.local.json` 에만 있어 `/Users/cogito` 에서 시작한 세션에는 적용되지 않는다 (전역 `~/.claude/settings.json` 설치 검토).
9. (해결) `~/.claude/settings.json` 의 `PreToolUse` matcher 를 `"*"` 로 올렸다 (2026-10-22, `hooks.install` 로 병합, 백업 `settings.json.bak-20261022-matcher`). 전에는 `Read|Bash|Write` 라 Edit·Grep 이 가드와 마무리 차단을 통과했다 (실제로 Edit 로 우회한 적 있음). 호출당 비용은 실측 70~90ms.
10. (해결) `session_start`·`wrapup_target`·`handoff_hint` 를 모두 `_marked_task` 기준으로 바꿨다 (2026-10-22). 표시 없는 effiency 에서 `/clear` 는 `pdf-professor` 대신 `.relay/HANDOFF-NEXT.md` 를 읽고, 그 파일이 없으면 주입하지 않는다. 새 task 로 작업할 때는 `relay task new` 가 `.relay/current` 를 쓴다 (`relay auto` 도 쓴다). 옛 task 로 돌아갈 때는 그 파일에 task 이름을 직접 적거나 `RELAY_TASK` 를 쓴다 — 표시를 바꾸는 명령은 없다.
