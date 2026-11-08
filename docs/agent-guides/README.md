# Agent operation guides

Claude Code와 Antigravity(Agy)가 이 저장소에서 같은 Relay 계약으로 작업하기
위한 진입점이다. 대화 내용이 아니라 `.relay/tasks/<id>/` 파일이 인수인계
단위다.

## Start here

1. 저장소 루트에서 `AGENTS.md`를 읽는다.
2. task id를 받았으면 `brief.md` → `state.md` → `pack.md`만 먼저 읽는다.
3. 역할별 가이드를 따른다.
   - Claude Code: [claude-code.md](claude-code.md)
   - Antigravity: [agy.md](agy.md)
4. 결과는 `.relay/tasks/<id>/out-<agent>.md`에 300단어 이내로 남긴다.

## Shared commands

```bash
cd ~/effiency
relay check --ping
relay task list
relay run <task-id> --dry-run
```

MCP가 연결되어 있으면 task 파일을 직접 편집하지 말고
`relay_task_read` / `relay_task_write`를 우선 사용한다.

## Execution environments

| 환경 | 인증 | 용도 |
|---|---|---|
| Mac local | 각 CLI의 기존 OAuth | 개발·전체 파이프라인 |
| AWS VM | VM의 각 CLI OAuth | GitHub self-hosted runner |
| GitHub-hosted runner | 로컬 OAuth 사용 불가 | API adapter가 있을 때만 |

AWS runner에서는 OAuth 로그인과 runner 서비스가 동일한 Linux 사용자와
`HOME`을 사용해야 한다. 인증 파일, device code, access/refresh token을 task,
로그, Git, Port 입력 또는 Artifact에 기록하지 않는다.

현재 실행 백엔드가 무엇인지 추측하지 말고 아래를 먼저 확인한다.

```bash
rg -n "runs-on|OPENAI_API_KEY|codex .*exec" .github/workflows relay
```

