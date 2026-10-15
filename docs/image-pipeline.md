# relay image — 의미 스펙 → Codex 이미지 생성

Claude 는 **무엇을 그릴지**만 짧은 JSON 으로 쓴다. 상세 프롬프트 작성과 이미지 생성은 Codex
(ChatGPT 로그인 쿼터)가 한다. Claude 가 수백 단어짜리 프롬프트를 쓰고 고치는 토큰이 0 이 된다.

```
Claude ── image-spec.json ──▶ Codex ── image-prompt.md (상세 프롬프트)
                                  └──▶ 내장 이미지 도구 ──▶ image.png ──▶ git ──▶ Claude 세션
```

## 스펙 (`.relay/tasks/<id>/image-spec.json`)

| 필드 | 뜻 |
|---|---|
| `subject` | 무엇을 그리는가 (필수) |
| `purpose` | 어디에 쓰는가 |
| `style` · `mood` · `composition` · `palette` · `lighting` | 의미 수준의 방향 |
| `text` | 그림 안 글자 (없으면 비움) |
| `size` | `1024x1024` · `1536x1024` · `1024x1536` |
| `avoid` | 넣지 말 것 (문자열 또는 목록) |
| `notes` | 기타 제약 |

알 수 없는 필드는 거부한다 (스펙에 프롬프트를 몰래 넣는 것을 막는다). 필드당 600자 제한.

## 실행 (codex 가 ChatGPT 로그인된 머신)

```bash
relay image 005                         # task 폴더의 image-spec.json 사용
relay image 005 --spec my-spec.json     # 스펙을 task 폴더로 복사한 뒤 실행
relay image 005 --print-prompt          # Codex 에 보낼 지시문만 확인
```

결과는 task 폴더에 남는다.

| 파일 | 내용 |
|---|---|
| `image.png` | 생성된 이미지 |
| `image-prompt.md` | Codex 가 펼친 상세 프롬프트와 필드별 해석 |
| `image.json` | 상태 · 러너 · 시도별 종료 코드와 출력 꼬리 |

Codex 는 `codex exec --sandbox workspace-write` 로 **task 폴더 안에서만** 쓴다. Codex 가 이미지를
task 폴더로 복사하지 못하면 relay 가 `$CODEX_HOME`(기본 `~/.codex`)에 실행 중 새로 생긴 이미지를 찾아
가져온다. 첫 러너 후보는 `-c features.image_generation=true` 로 이미지 기능을 켜고, 그 플래그를
모르는 codex 버전에서 실패하면 플래그 없는 후보로 다시 시도한다 (`.relay/config.toml` 의
`[runners] image` 로 바꿀 수 있다). Codex 에 이미지 도구가 없으면 `IMAGE_TOOL_UNAVAILABLE` 로 멈추고
`image-prompt.md` 만 남긴다.

## 클라우드 Claude 세션과 주고받기

클라우드 세션 컨테이너에는 Codex 로그인이 없고 api.openai.com 도 막혀 있다. 그래서 인수인계는 git 이다.

1. Claude 세션: `image-spec.json` 을 커밋 · 푸시
2. VM: `git pull && relay image <id>` → 결과 커밋 · 푸시
3. Claude 세션: `git pull` 로 `image.png` 확인
