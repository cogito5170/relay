# relay ask — PDF 에서 드래그해서 질문하기

미리보기 · Chrome PDF 에서 궁금한 부분을 드래그 → 단축키 → 질문 → 브라우저에 설명 페이지 (수식 KaTeX, 그림 Mermaid).

## 설치 (한 번)

```bash
brew install poppler
cd ~/effiency
relay ask --setup
```

`--setup` 이 출력하는 안내대로 단축어 앱에서 빠른 동작을 만들고 단축키(예: ⌥⌘E)를 지정한다.
`poppler`(pdftotext)는 선택한 문장이 있는 쪽을 찾아 주변 문단을 붙이는 데 쓴다. 없으면 선택한 부분만 보낸다.

## 쓰는 법

1. PDF 에서 문장이나 수식을 드래그
2. 단축키 → 질문 입력 (기본값 "쉽게 설명해줘")
   - 앞에 `!` 를 붙이면 깊게 (Claude): `!이 식을 유도해줘`
3. 잠시 뒤 브라우저에 설명 페이지가 열린다. 모든 답은 `notes/papers/<문서>/qa.md` 에도 쌓인다.

터미널에서 직접: `relay ask --source 논문.pdf --question "쉽게 설명해줘" --text "선택한 문장" --open`

## 토큰 분산

| 단계 | 담당 | Claude 토큰 |
|---|---|---|
| 선택 문장이 있는 쪽 찾기 · 주변 문단 3,000자 자르기 | 로컬 (pdftotext) | 0 |
| 쉬운 설명 | agy → codex (실패하면 다음, 마지막에 Claude) | 0 |
| 깊은 질문 (`!`, 유도·증명, 수식 위주 선택) | Claude 1턴, 도구 없음, 짧은 system prompt | 질문당 수천 |
| 다시 보기 | `qa.md` · `answers/*.html` | 0 |

설정은 `.relay/config.toml` 의 `[ask]` (모델, 맥락 글자 수, 러너 순서).

## 한계

- PDF 의 수식은 텍스트로 깨져서 추출되는 경우가 많다. 이때는 질문에 "수식이 깨졌으면 맥락으로 추정해서" 라고 덧붙이거나 `!` 로 깊게 묻는다.
- 그림은 원본 재현이 아니라 설명용 도식(Mermaid)이다.
- 미리보기에서 PDF 경로를 못 가져오면(권한 거부 등) 선택한 부분만으로 답한다. Chrome 에서 연 웹 PDF 는 `~/.relay/pdf-cache` 로 내려받아 쪽을 찾는다.
- 검증: 단위 테스트 + 실제 Claude(Haiku) 깊은 질문 1회. 빠른 동작(단축어) 자체와 agy/codex 경로는 맥에서 첫 사용 때 확인 필요.
