# STATE — 2026-10-13 세션 기록

브랜치 `claude/zealous-goodall-wkb3yz`. 상세 배경은 `docs/HANDOFF.md`, 측정값은 `docs/pipeline.md` "실측 2".

## 한 일

`relay run` 을 실제 CLI 로 처음 끝까지 돌렸다 (gemini-cli 0.46.0, codex-cli 0.152.0, claude 리뷰 sonnet/medium). 작업 `001` 에서 codex 가 구현하고 Claude 가 APPROVE 했으며, 1회차에 codex 가 아무것도 고치지 못한 것을 리뷰가 잡아냈다.

실행에서 드러난 문제 세 가지를 고쳤다.

1. `codex exec --full-auto` 는 codex-cli 0.152 에서 사라졌다. 기본값을 `codex exec --sandbox workspace-write` 로 바꿨다.
2. codex 는 사이드카 `codex-code-mode-host` 를 실행 파일과 같은 디렉터리에서 찾는다. `~/.local/bin` 에 두 바이너리를 모두 링크했다.
3. Gemini 가 503 으로 죽으면 explore 가 통째로 날아갔다. 러너가 0 이 아닌 코드로 끝나면 다음 후보로 재시도하도록 `installed_runners` 를 추가했다.

변경: `relay/config.py`, `relay/pipeline.py`, `relay/cli.py`, `tests/test_pipeline.py` (51개 통과), `docs/pipeline.md`, `docs/HANDOFF.md`, `.gitignore`. 커밋하지 않았다.

## 결정

- 리뷰 비용의 실측치는 가중 약 15.6k (컨텍스트 35.8k, 3턴) 이며, 기존 문서의 5.5~6.5k 는 1턴 대역 스크립트 기준이었다. 비용은 턴 수가 지배하므로 brief 와 diff 를 명확히 쓰는 쪽이 가장 큰 레버다.
- `--max-turns` 는 아직 8 로 둔다. 낮추면 VERDICT 가 잘릴 위험이 있어 별도 실험이 필요하다.
- `relay init` 산출물(`.relay/`, `.claude/`, `.agent/`, `AGENTS.md`, `CLAUDE.md`)은 머신마다 달라 git 에서 제외한다.

## 다음 단계

1. 변경분을 `v0.4.1` 로 커밋할지 결정.
2. 리뷰 턴 수 줄이기 실험 (`--max-turns 4`, system prompt 에 턴당 재과금 명시).
3. Gemini 503 이 잦으면 explore 1순위를 `codex exec --sandbox read-only` 로 교체.
4. `relay lean` 상시 사용, hooks 를 Sonnet/Opus 에서 확인, `relay why` 미분류 19% 분석.
