# VM 인박스 — VM 에 접근할 수 없는 사람도 VM 의 codex · agy 를 쓰는 법

codex 와 agy 는 VM 에 OAuth 로 로그인되어 있다. Claude Code 사용자(클라우드 세션 포함)는 VM 에
들어갈 수 없다. 그래서 **git 저장소를 우편함으로 쓴다**: 요청 파일을 브랜치에 푸시하면 VM 의 워커가
가져가 실행하고, 결과를 같은 브랜치에 커밋해 푸시한다.

```
Claude Code (어디서든) ── push request.json ──▶ GitHub ◀── 30초마다 fetch ── VM 워커
                       ◀── pull 결과 ────────────────────── push response.json + 결과 ──┘
```

VM 은 GitHub 로 나가는 연결만 쓴다. 열리는 포트가 없고, 요청 파일은 정해진 세 가지 모양만 허용하며
셸을 거치지 않는다. 워커(`relay inbox`)는 클론을 요청 브랜치로 체크아웃하므로, 클론 안의 코드가 아니라 VM 에 **설치된** relay 를 실행한다.

## 요청 (Claude Code 쪽)

```bash
relay request 005 image                                   # image-spec.json → Codex 이미지
relay request 005 advise                                  # agy 읽기 전용 자문 (brief/pack 기반)
relay request 005 delegate --agent codex --instruction "tests/ 에 X 테스트 추가"
git add .relay/tasks/005-* && git commit -m "relay request 005" && git push
# 잠시 뒤
git pull   # → .relay/tasks/005-*/response.json (status · exit_code · 출력 꼬리) 와 결과 파일
```

| action | VM 에서 실행 | 커밋되는 것 |
|---|---|---|
| `image` | `relay image <task>` | task 폴더 (`image.png`, `image-prompt.md`, `image.json`) |
| `advise` | `relay advise <task> --agent agy` | task 폴더 (`out-agy.md`) |
| `delegate` | `relay delegate <codex\|agy> "<instruction>"` | 작업 트리의 모든 변경 |

같은 요청을 다시 보내려면 `relay request` 를 다시 실행한다 (`id` 가 바뀌어 새 요청이 된다).
워커는 `response.json` 의 `request_sha256` 이 지금 `request.json` 과 같으면 건너뛴다.

## VM 설치 (한 번, VM 소유자)

1. relay 설치 (Ubuntu/Debian 의 시스템 파이썬은 pip 직접 설치를 막으므로 pipx 로):
   ```bash
   sudo apt install -y pipx && pipx ensurepath   # 새 셸 열기
   pipx install git+https://github.com/cogito5170/effiency.git   # → ~/.local/bin/relay
   ```
   업데이트는 같은 명령에 `--force`.
2. 워커 전용 클론 (작업용 체크아웃과 분리):
   ```bash
   mkdir -p ~/relay-inbox && git clone git@github.com:cogito5170/effiency.git ~/relay-inbox/repo
   git -C ~/relay-inbox/repo config user.name "relay inbox" && git -C ~/relay-inbox/repo config user.email "relay-inbox@localhost"
   ```
   푸시 권한은 이 저장소에만 쓰기 권한이 있는 **deploy key** 로 준다 (Settings → Deploy keys → Allow write access).
3. 한 번 수동 실행해 확인: `relay inbox --repo ~/relay-inbox/repo` (마지막 줄에 확인한 브랜치·처리한 요청 수가 나온다)
4. 타이머 등록:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/relay-inbox/repo/scripts/systemd/relay-inbox.{service,timer} ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now relay-inbox.timer
   loginctl enable-linger "$USER"     # 로그아웃해도 계속 실행
   journalctl --user -u relay-inbox -f   # 로그
   ```

기본으로 `claude/*` 브랜치만 본다 (`--branch` 로 추가). 클라우드 Claude 세션은 자기 `claude/...` 브랜치에만
푸시할 수 있으므로 이 기본값이면 된다.

## 다른 저장소도 보기 (예: gentleMonster)

워커 하나가 여러 저장소를 본다: `--repo` 를 반복하면 30초마다 차례로 확인한다. 클론이 없는 저장소는 건너뛰고
나머지는 계속 처리한다. 요청하는 쪽 저장소에는 relay 설정이 없어도 된다 (`relay image` 는 task 폴더만 쓴다).

1. 저장소마다 deploy key 를 따로 만든다 (GitHub 는 같은 키를 두 저장소에 등록하지 못한다):
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/relay_gentlemonster
   cat >> ~/.ssh/config <<'CFG'
   Host github-gentlemonster
     HostName github.com
     User git
     IdentityFile ~/.ssh/relay_gentlemonster
     IdentitiesOnly yes
   CFG
   cat ~/.ssh/relay_gentlemonster.pub   # → gentleMonster Settings → Deploy keys → Allow write access
   ```
2. 워커 전용 클론:
   ```bash
   git clone git@github-gentlemonster:cogito5170/gentleMonster.git ~/relay-inbox/gentleMonster
   git -C ~/relay-inbox/gentleMonster config user.name "relay inbox" && git -C ~/relay-inbox/gentleMonster config user.email "relay-inbox@localhost"
   ```
3. 확인 후 타이머에 반영 (`scripts/systemd/relay-inbox.service` 에 이미 두 저장소가 들어 있다):
   ```bash
   relay inbox --repo ~/relay-inbox/repo --repo ~/relay-inbox/gentleMonster
   cp ~/relay-inbox/repo/scripts/systemd/relay-inbox.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user restart relay-inbox.timer
   ```

요청하는 저장소가 `.relay/` 를 `.gitignore` 로 숨기면 `.relay/tasks/` 는 예외로 열어 둔다
(`!.relay/tasks/`). 워커는 결과를 `git add -f` 로 올리지만, 요청하는 세션의 `request.json` 이 올라가야 하기 때문이다.

## 다른 방법을 고르지 않은 이유

- **GitHub Actions self-hosted runner**: 이 저장소는 public 이다. GitHub 는 public 저장소에
  self-hosted runner 를 붙이지 말라고 권고한다 (포크 PR 의 워크플로가 VM 에서 실행될 수 있다).
  저장소를 private 으로 바꾸면 workflow_dispatch 로 즉시 실행되는 좋은 대안이 된다.
- **VM 에 MCP/HTTP 브리지를 열기**: 인바운드 포트 · 터널 · 인증이 필요하고, 클라우드 세션의 네트워크
  정책이 임의 호스트를 막는다.
