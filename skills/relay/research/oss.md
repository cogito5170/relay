# relay 설계 근거: 비슷한 문제를 푸는 오픈소스 10개

확인 날짜: 2026-11-08. 별 수와 마지막 푸시(pushed_at)는 GitHub API(`api.github.com/repos/...`)에서 직접 읽은 값이다. 저장소는 clone하지 않았고 README·문서의 raw 파일이나 문서 사이트 HTML만 읽었다. 읽지 못한 부분은 `[미확인]`으로 표시했다. 인용문은 원문(영어) 그대로이며 번역하지 않았다.

용어 풀이(처음 등장 기준): 컨텍스트 창(context window, 모델이 한 번에 보는 입력 범위), 인수인계(handoff), 저장소 지도(repo map), 명세 주도 개발(Spec-Driven Development, SDD), 기억 블록(memory block), 마이크로에이전트/스킬(microagent/skill, 조건부로 읽는 지침 파일), 프런트매터(frontmatter, 파일 맨 위 메타데이터 칸).

## 비교 표

| 저장소 | 상태 위치 | 로드 시점 | 완료 판정 | 멀티 에이전트 | 차용할 점 |
|---|---|---|---|---|---|
| Cline Memory Bank | `memory-bank/*.md` 6개 | 매 작업 시작 시 전부 읽기 | 없음(사람 판단) | 없음(단일 도구) | 파일 간 의존 순서와 "update" 체크포인트 |
| Roo Code | `.roo/` 규칙·명령, 모드 | [미확인] | [미확인] | 모드 전환(역할 분리) | 역할(모드)별 도구 제한 아이디어. 단, 2026-05-15 종료 |
| Aider | `CONVENTIONS.md`, 저장소 지도(자동 생성) | 지도는 요청마다, 규약은 `--read`로 읽기 전용 | 없음(사람 판단) | 없음 | 토큰 예산 안에서 순위로 고르는 지도(= relay pack 근거) |
| claude-task-master | `tasks.json` + 작업별 파일 | 명령 실행 시 | `testStrategy` 문장(실행 명령 아님) | 모델 슬롯 3개(main/research/fallback) | 복잡도 점수로 쪼개기, 폴백 모델 |
| GitHub spec-kit | `specs/<브랜치>/{spec,plan,tasks}.md`, `.specify/memory/constitution.md` | 단계 명령이 앞 산출물을 입력으로 읽음 | `Converged` 판정, 체크리스트 | 여러 코딩 에이전트 전환 지원 | 헌법(constitution) 게이트, `[P]` 병렬 표시 |
| BMAD-METHOD | [미확인] 경로 | [미확인] | [미확인] | 스킬 명령 + 여러 에이전트 토론 | 기존 코드베이스용 project-context 단계 |
| Letta (MemGPT) | 기억 블록(에이전트 서버·클라우드 저장) | 항상 컨텍스트에 포함 | 없음 | 블록 공유(Shared memory) | 읽기 전용 블록, 블록 설명(description) 필드 |
| mem0 | 벡터 저장소(사용자·세션·에이전트 수준) | 질의 시 검색 | 없음 | 에이전트 수준 상태 | 추가 전용(ADD-only) 기록, 검색 시 시간 인식 |
| OpenHands | `.agents/skills/*.md` + `AGENTS.md` | 트리거가 맞을 때(`triggers:`) | [미확인] | [미확인] | 프런트매터 트리거로 필요 시 로드 |
| AGENTS.md 표준 | 저장소 루트와 하위 폴더의 `AGENTS.md` | 에이전트가 가장 가까운 파일 읽음 | 파일에 적은 테스트 명령을 실행 | 60개 안팎 도구 공용 | 가까운 파일 우선 규칙, 단일 공용 파일명 |

## 1. Cline Memory Bank

- URL: https://github.com/cline/cline (문서 `docs/best-practices/memory-bank.mdx`). 별 71456, 마지막 푸시 2026-11-07.
- 상태·기억: `memory-bank/` 폴더에 `projectbrief.md`(기초), `productContext.md`, `activeContext.md`(현재 초점, 가장 자주 갱신), `systemPatterns.md`, `techContext.md`, `progress.md`(진행·남은 일·알려진 문제). 파일은 위에서 아래로 의존한다.
- 읽고 쓰는 때: 사용자 지시문(custom instructions)이 매 작업 시작에 전부 읽게 한다(항상 로드). 쓰기는 "update memory bank" 요청 시 전체 검토, `activeContext.md`는 세션마다.
- 작업 단위·완료: 작업 카드나 완료 판정 규칙 없음. 사람이 판단.
- 인수인계: 같은 도구가 다음 세션에서 같은 파일을 읽는 방식. 도구 간 인수인계는 [미확인].
- 근거: "I MUST read ALL memory bank files at the start of EVERY task - this is not optional." / "Memory Bank is a documentation methodology that transforms Cline from a stateless assistant into a persistent development partner." (`docs/best-practices/memory-bank.mdx`)
- relay와 비교: 좋은 점은 파일 종류별 갱신 빈도가 다르다는 설계(activeContext만 자주). relay는 완료 판정(`done_when`)·크기 제한·검사기가 있어 낫다. relay는 6개를 매번 다 읽지 않아 컨텍스트가 덜 든다. 차용: 상태 파일을 "자주 바뀌는 것 / 거의 안 바뀌는 것"으로 나누고 후자는 pickup에서 필요 시만 읽기.

## 2. Roo Code

- URL: https://github.com/RooCodeInc/Roo-Code. 별 24238, 마지막 푸시 2026-05-15.
- 상태: 모드(Code, Architect, Ask, Debug, 사용자 정의)로 역할을 나눈다. 저장소에 `.roo/commands`, `.roo/guidance` 폴더가 있음을 목록에서 확인했다. 모드 설정 파일·메모리 형식은 [미확인].
- 완료 판정·인수인계: [미확인]. 모드 전환이 역할 간 넘김에 해당하나 파일 규약은 읽지 못했다.
- 근거: "Adapt with Modes: Code, Architect, Ask, Debug, and Custom Modes" / "The Roo Code Extension was shut down on May 15th." (README.md)
- relay와 비교: 좋은 점은 역할별 모드 아이디어(relay의 `model` 필드와 비슷하나 도구 권한까지 묶음). 이 저장소는 종료되어 장기 근거로는 약하다. 차용: 카드의 `scope/never`를 역할 단위 템플릿으로 묶기.

## 3. Aider

- URL: https://github.com/Aider-AI/aider. 별 50019, 마지막 푸시 2026-05-22.
- 상태: 대화 상태 파일은 없고 저장소 지도(repo map)를 자동 생성한다. 규약은 `CONVENTIONS.md`를 `/read`나 `.aider.conf.yml`로 읽힌다.
- 읽는 때: 지도는 요청마다 모델에 전송, 규약은 읽기 전용으로 로드하고 프롬프트 캐시(prompt caching)가 켜져 있으면 캐시.
- 완료 판정: 문서에서 확인한 규칙 없음. 멀티 에이전트: 없음(단, AGENTS.md를 `read: AGENTS.md` 설정으로 읽을 수 있다고 AGENTS.md 사이트가 안내).
- 근거: "Aider sends a **repo map** to the LLM along with each change request from the user." / "The token budget is influenced by the --map-tokens switch, which defaults to 1k tokens." (`aider/website/docs/repomap.md`, 후자는 repomap 페이지 본문) 및 "It's best to load the conventions file with `/read CONVENTIONS.md`... This way it is marked as read-only, and cached if prompt caching is enabled." (`docs/usage/conventions.md`)
- relay와 비교: 지도는 그래프 순위 알고리즘으로 중요한 기호만 토큰 예산에 맞춘다. relay의 pack(예산 안 코드 조각)과 같은 발상이며 Aider가 자동화가 더 성숙했다. relay는 사람이 읽는 상태·카드가 있어 세션을 넘긴다. 차용: pack 생성에 "참조 그래프 순위 + 토큰 예산" 사용, 규약 파일은 읽기 전용·캐시 친화 위치에 고정.

## 4. claude-task-master

- URL: https://github.com/eyaltoledano/claude-task-master. 별 28324, 마지막 푸시 2026-04-28.
- 상태: `tasks.json`(필드 id, title, description, status, dependencies, priority, details, testStrategy, subtasks)과 작업별 텍스트 파일. 기본 PRD 위치 `.taskmaster/docs/prd.txt`.
- 읽고 쓰는 때: 명령(`parse-prd`, `expand`, `set_task_status` 등)이 실행될 때. 항상 로드 여부는 [미확인].
- 완료 판정: `status`가 `done`으로 바뀌는 것. `testStrategy`는 자연어 설명이며 실행되지 않는다.
- 인수인계: 모델 슬롯(main, research, fallback). 도구 간 파일 공유는 README에서 확인 못 함.
- 근거: "`testStrategy`: Verification approach (Example: `"Deploy and call endpoint to confirm 'Hello World' response."`)" (`docs/task-structure.md`) / "You can define 3 types of models to be used: the main model, the research model, and the fallback model" (README.md)
- relay와 비교: relay의 `done_when`(셸 명령, 종료 0)이 기계 검증이라 더 낫다. `scope/never`, `needs: human`도 task-master에 없다. 좋은 점은 복잡도 1~10 점수를 매겨 작업을 자동 분할(`analyze-complexity`)하는 것과 폴백 모델. 차용: 모델 호출 실패 시 `model` 폴백을 카드 필드로 추가.

## 5. GitHub spec-kit

- URL: https://github.com/github/spec-kit. 별 148567, 마지막 푸시 2026-11-07.
- 상태: `specs/<브랜치명>/spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md, tasks.md`, 그리고 `.specify/memory/constitution.md`(불변 원칙). 버그·평가 산출물은 `.specify/bugs/<slug>/`, `.specify/assessments/<slug>/`.
- 읽고 쓰는 때: 단계 명령이 앞 단계 산출물을 입력으로 읽고 다음 파일을 쓴다. 헌법은 계획 단계의 게이트로 검사.
- 완료 판정: 구현과 수렴(converge)을 되풀이해 `Converged`가 나올 때까지. 버그는 `verified`만 성공. 명세 체크리스트를 "unit tests"로 부른다.
- 인수인계: 여러 코딩 에이전트 연결·전환 지원(README 기능 항목). 파일 규약은 같은 `specs/` 폴더를 공유.
- 근거: "Repeat implement and converge until convergence reports `Converged`." (README.md) / "Writes `tasks.md` in the feature directory, ready for execution by a Task agent" (`spec-driven.md`)
- relay와 비교: 좋은 점은 헌법 게이트, `[P]` 병렬 표시, 판정값을 열거형(verified/partial/failed)으로 구분. relay는 상태 파일 크기 제한·압축 훅이 있고 `done_when`이 실행 명령이라 더 기계적이다. 차용: 완료 결과를 `done/partial/failed` 3값으로 기록하고 `partial`을 오래된 대기로 검사.

## 6. BMAD-METHOD

- URL: https://github.com/bmad-code-org/BMAD-METHOD. 별 55123, 마지막 푸시 2026-11-08.
- 상태: 문서 인덱스는 "skills"라는 이름 붙은 명령을 Claude Code·Cursor 같은 도구에 추가한다고 설명. 산출물 경로·형식은 README와 `docs/index.md`에서 확인 못 함 `[미확인]`.
- 완료 판정·로드 시점·인수인계 파일 규약: [미확인].
- 근거: "Carry product and technical decisions forward instead of re-explaining them in every chat." (README.md) / "BMad adds a set of named commands, called skills, to AI coding tools such as Claude Code and Cursor." (`docs/index.md`)
- relay와 비교: 기존 코드베이스에서 `bmad-project-context`로 맥락을 먼저 정리하는 단계가 있다고 인덱스에 적혀 있다. relay는 이 부트스트랩 단계가 없다. 차용: 처음 도입 시 프로젝트 맥락 파일을 한 번 생성하는 `/relay init`(세부는 문서 더 읽고 판단).

## 7. Letta (MemGPT)

- URL: https://github.com/letta-ai/letta (현재 소스는 https://github.com/letta-ai/letta-code, 별 3803, 푸시 2026-11-08). letta-ai/letta는 별 25386, 마지막 푸시 2026-10-18.
- 상태: 기억 블록(memory block)을 에이전트 서버·클라우드에 저장. 블록마다 label과 description이 있고 읽기 전용 블록과 공유 블록이 있다(문서 목차 확인, 세부 필드는 [미확인]).
- 읽는 때: 항상. 블록은 프롬프트 앞에 XML 비슷한 형식으로 붙는다. 장기 기억은 별도 보관소(Archival memory)로 검색.
- 완료 판정: 해당 없음. 인수인계: 블록을 여러 에이전트가 공유(Shared memory).
- 근거: "Memory blocks are structured sections of the agent's context window that persist across all interactions. They are always visible - no retrieval needed." / "Create a block with a descriptive label and the agent learns how to use it." (https://docs.letta.com/guides/agents/memory-blocks) 및 README.md: "Build stateful agents with memory that can learn and improve over time."
- relay와 비교: 좋은 점은 "항상 보이는 작은 블록 + 필요 시 검색하는 보관소"의 2층 구조와 블록 설명 필드. relay의 archive/recall 훅이 같은 구조이고 파일이라 사람이 열어 볼 수 있어 더 낫다. 차용: state 파일 각 절에 한 줄 목적 설명을 두고, 읽기 전용 절(brief)은 에이전트가 못 고치게 표시.

## 8. mem0

- URL: https://github.com/mem0ai/mem0. 별 68618, 마지막 푸시 2026-11-06.
- 상태: 벡터 검색용 기억 계층. 사용자·세션·에이전트 수준으로 나눔. 파일 경로 형식은 해당 없음.
- 읽고 쓰는 때: 호출 시 SDK로 추가·검색. 2026-04 알고리즘은 한 번의 LLM 호출로 추출하고 갱신·삭제 없이 쌓는다.
- 완료 판정·멀티 에이전트: 완료 판정 없음. 에이전트 수준 상태가 있으나 도구 간 인수인계 방식은 [미확인].
- 근거: "Multi-Level Memory: Seamlessly retains User, Session, and Agent state with adaptive personalization" / "Single-pass ADD-only extraction -- one LLM call, no UPDATE/DELETE. Memories accumulate; nothing is overwritten." (README.md)
- relay와 비교: 좋은 점은 추가 전용 기록과 시간 인식 검색(현재 상태 대 과거 사건 구분). relay의 archive는 원문 보관이라 비슷하다. 성능 수치는 관리형 플랫폼 기준이라 relay와 직접 비교 불가. 차용: archive를 추가 전용으로 하고 항목마다 날짜를 붙여 recall이 최신 것을 우선하게 하기.

## 9. OpenHands

- URL: https://github.com/OpenHands/OpenHands. 별 92931, 마지막 푸시 2026-11-08.
- 상태: 현재 저장소에 `.agents/skills/<이름>.md`와 `AGENTS.md`가 있다. 프런트매터에 `name`, `description`, `triggers`(예: `/codereview`)가 있다. 과거 이름 microagents는 이번에 확인 못 함.
- 읽는 때: 트리거가 맞을 때 읽는 조건부 로드. 해당 스킬 파일은 "Read `AGENTS.md` first"라고 지시한다.
- 완료 판정·멀티 에이전트: README는 ACP 에이전트 지원과 자동화를 언급하나 세부는 [미확인].
- 근거: 스킬 파일 프런트매터 `triggers:` 아래 `- /codereview` / "Read `AGENTS.md` first; it is the detailed source of truth for current architecture and test conventions." (`.agents/skills/custom-codereview-guide.md`)
- relay와 비교: 좋은 점은 지침 파일을 프런트매터 트리거로 필요할 때만 읽게 한 것. relay의 훅이 크기를 막는 방식과 다른 접근이라 보완이 된다. 차용: 카드나 스킬에 `triggers`를 두고 pickup이 해당 카드 관련 지침만 읽기.

## 10. AGENTS.md 표준

- URL: https://github.com/agentsmd/agents.md, 표준 설명은 https://agents.md. 별 25392, 마지막 푸시 2026-09-10. 저장소 루트의 `AGENTS.md`는 그 저장소 자신의 지침이므로 표준 근거로 쓰지 않았다.
- 상태: 저장소 루트(큰 모노레포는 하위 폴더에도) 평문 Markdown. 필수 필드 없음.
- 읽는 때: 에이전트가 편집 대상에 가장 가까운 파일을 읽는다. 충돌 시 가장 가까운 파일, 그다음 사용자 채팅 지시가 이긴다.
- 완료 판정: 파일에 테스트 명령을 적으면 에이전트가 실행하고 실패를 고친 뒤 끝낸다고 FAQ가 설명. 사람/모델이 판단하지 않고 명령이 기준.
- 인수인계: 여러 도구가 같은 파일명을 읽는다(Codex, Jules, Aider, Cursor, Gemini CLI 등 사이트 목록). Aider·Gemini CLI는 설정으로 파일명을 지정.
- 근거: "Think of AGENTS.md as a README for agents: a dedicated, predictable place to provide the context and instructions to help AI coding agents work on your project." / "The closest AGENTS.md to the edited file wins; explicit user chat prompts override everything." (https://agents.md)
- relay와 비교: 좋은 점은 "도구마다 다른 파일명"을 하나로 통일해 멀티 도구 문제를 가장 단순하게 푼 것. relay는 `.relay/` 구조가 더 풍부하지만 각 도구가 자동으로 읽는다는 보장이 없다. 차용: 루트 `AGENTS.md`에 "먼저 `.relay/HANDOFF-NEXT.md`를 읽어라" 한 줄을 두어 모든 도구가 진입하게 하기.

## relay가 꼭 차용해야 할 상위 3개

1. 루트 `AGENTS.md`를 모든 도구의 공통 진입점으로 쓰고 거기서 `.relay/`를 가리키기(AGENTS.md 표준, 가장 가까운 파일 우선 규칙).
2. 토큰 예산 안에서 참조 순위로 고르는 pack 생성과 규약 파일의 읽기 전용·캐시 친화 고정(Aider 저장소 지도, `--map-tokens` 기본 1k).
3. 완료 결과를 3값(done/partial/failed 또는 verified/partial/failed)으로 기록하고 헌법식 게이트로 검사(spec-kit). task-master의 폴백 모델은 4위 후보.

## 확인 못 한 것

- Roo Code의 모드 설정 파일 형식과 메모리 방식, BMAD의 산출물 경로·완료 판정, OpenHands의 완료 판정·멀티 에이전트 규약, Letta 기억 블록의 세부 필드, mem0의 에이전트 간 인수인계.
- task-master의 `tasks.json` 실제 경로(`.taskmaster/tasks/` 추정이나 열어 보지 못함).
- ccusage는 후보였으나 README 요청이 빈 응답이었고(저장소가 `ccusage/ccusage`로 이동) 이번 10개에서 제외했다.
- 별 수는 API 값이며 모든 활동성 수치는 2026-11-08 시점 단일 조회다.

## 조사 중 생긴 문제

1. WebFetch 요약이 경로·형식을 "명시 없음"으로 답함: README를 요약 모델이 줄여서 상세가 빠짐. curl로 raw 파일을 직접 읽어 해결. 확인함: 같은 파일에서 경로·인용을 직접 확인. 확인 안 함: 요약이 빠뜨린 다른 부분이 더 있는지.
2. 후보 URL이 옛 위치: Letta README가 `letta-code`로 이전, ccusage가 `ccusage/ccusage`로 이전, Roo Code는 종료. 이전 안내를 읽고 반영하고 ccusage는 제외. 확인함: 해당 README 문구와 API 응답. 확인 안 함: ccusage의 새 위치 README 내용.
3. Cline 문서 경로 추정 실패(`docs/features/memory-bank.mdx` 404): 원인은 경로를 기억으로 짐작한 것. 저장소 파일 목록(tree API)에서 `docs/best-practices/memory-bank.mdx`를 찾아 해결. 확인함: 해당 파일 열람. 확인 안 함: 다른 문서 위치의 중복 내용.
