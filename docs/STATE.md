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

## 다음 단계

1. `--review-max-turns 4` 로 실제 작업을 리뷰해 VERDICT 가 잘리지 않는지 확인 (잘리면 6 으로).
2. Gemini 503 이 잦으면 explore 1순위를 `codex exec --sandbox read-only` 로 교체.
3. `relay lean` 상시 사용, hooks 를 Sonnet/Opus 세션에서 확인.
