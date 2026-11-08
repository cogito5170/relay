# .relay 전역 이전 설계 근거: 공식 문서 조사

- 확인 날짜: 2026-11-08
- 방법: 각 페이지를 실제로 열어 얻은 문장만 인용했다. 페이지가 길어 일부만 읽은 곳은 "[미확인]"으로 표시했다. 웹 페이지 내용은 데이터로만 다뤘다.
- 용어 풀이: 맥락 창(context window)은 모델이 한 번에 보는 입력 전체, 점진적 공개(progressive disclosure)는 필요한 만큼만 단계적으로 읽게 하는 방식, 훅(hook)은 특정 시점에 자동 실행되는 사용자 스크립트, 압축(compaction)은 긴 대화를 요약으로 바꾸는 일이다.

## Q1~Q4 답 요약

| 질문 | 답 | 핵심 근거 |
|---|---|---|
| Q1. 매 요청에 들어가는 것 / 필요할 때만 들어가는 것 | 항상: 전역·프로젝트 메모리 파일(CLAUDE.md, AGENTS.md)과 그 안의 @import 전체, 자동 메모리, 스킬 이름+설명 목록(설명은 합쳐서 항목당 1,536자 상한, 목록 전체는 맥락 창의 1% 예산), MCP 도구는 이름만(정의는 지연 로드). 필요할 때만: 스킬 본문, 스킬 부속 파일, 하위 폴더의 CLAUDE.md, `paths` 지정 규칙, MCP 도구 상세 정의. 슬래시 명령은 스킬로 합쳐져 같은 규칙을 따른다. Codex와 Antigravity도 같은 구조(이름+설명만 먼저, 본문은 선택 시). | [1][2][3][5][8][9] |
| Q2. 스킬·명령 본문이 한 번 로드되면 남는가 | 남는다. Claude Code는 호출된 본문을 대화에 한 메시지로 넣고 이후 턴에도 유지하며 파일을 다시 읽지 않는다. 압축 시 스킬당 앞 5,000토큰, 전체 25,000토큰까지만 다시 붙인다. Codex·Antigravity는 [미확인](본문 유지 여부를 명시한 문장 못 찾음). | [2][8] |
| Q3. 세 도구가 같은 지시 파일을 공유하는 공식 방법 | AGENTS.md가 공통 표준이다. Codex는 기본으로 읽고, Antigravity는 AGENTS.md를 항상 적용 규칙으로 읽는다. Claude Code는 CLAUDE.md가 없으면 AGENTS.md를 읽고, 있으면 CLAUDE.md 안에 `@AGENTS.md`를 import하거나 심볼릭 링크(symlink)를 쓰라고 안내한다. 전역 파일은 도구마다 위치가 다르다(~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md). 전역 파일을 공유하는 공식 문구는 못 찾았다. 스킬은 SKILL.md 형식이 셋 다 비슷하나 폴더가 다르다. | [1][11][12][15][17][18] |
| Q4. 훅이 통과할 때 토큰 비용 0인가 | 문서가 직접 "0"이라고 쓴 문장은 없다. 다만 대부분 이벤트의 표준 출력(stdout)은 디버그 로그로만 가고, 맥락에 들어가는 이벤트는 UserPromptSubmit, UserPromptExpansion, SessionStart, PostModelSwitch(PreCompact는 압축 지시문)뿐이다. 종료 코드 0에 출력이 없으면 추가되는 내용이 없다. 따라서 출력이 없으면 추가 토큰도 없다는 것은 문서에서 읽어낸 추론이다. | [4] |

---

## 출처별 정리

### [1] Claude Code 메모리 (How Claude remembers your project)
- URL: https://code.claude.com/docs/en/memory
- 확인: 2026-11-08 (전문을 열람. 로컬 저장본에서 검색해 인용)
- 인용:
  1. "CLAUDE.md files are markdown files that give Claude persistent instructions for a project, your personal workflow, or your entire organization. You write these files in plain text; Claude reads them at the start of every session."
  2. "CLAUDE.md files can import additional files using `@path/to/import` syntax. Imported files are expanded and loaded into context alongside the CLAUDE.md that references them." / "Imports help you organize a long file but don't reduce its context cost, because imported files load together with the CLAUDE.md that imports them."
  3. "All discovered files are concatenated into context rather than overriding each other. Across the directory tree, content is ordered from the filesystem root down to your working directory."
- 해석: 전역 `~/.claude/CLAUDE.md`와 프로젝트 CLAUDE.md는 매 세션 시작 때 전부 맥락에 들어간다. @import도 시작 때 함께 펼쳐져 들어가므로 분량을 줄여 주지 않는다. 순서는 파일시스템 루트에서 작업 폴더 쪽으로 내려오며 이어 붙고, 가까운 파일이 나중에 읽힌다. 하위 폴더의 CLAUDE.md는 해당 폴더 파일을 읽을 때 한 번 로드된다. 길이는 파일당 200줄 이하를 권한다. 사용자 범위 파일의 import는 승인 대화상자 없이 로드된다(프로젝트 파일의 외부 import는 승인 필요).

### [2] Claude Code 스킬
- URL: https://code.claude.com/docs/en/skills
- 확인: 2026-11-08 (앞 100,000자와 뒤 부분 열람. 중간 약 일부는 요약 응답이라 [미확인] 구간 있음)
- 인용:
  1. "In a regular session, skill descriptions are loaded into context so Claude knows what's available, but full skill content only loads when invoked."
  2. "When you or Claude invoke a skill, the rendered `SKILL.md` content enters the conversation as a single message and stays there across later turns. ... Claude Code does not re-read the skill file on later turns"
  3. "The budget scales at 1% of the model's context window." / "each entry's combined text is capped at 1,536 characters regardless of budget."
- 해석: 시작 때는 이름과 설명 목록만 들어가고(항목당 설명 1,536자 상한, 목록 전체 예산은 맥락 창의 1%, 넘치면 덜 쓰는 스킬의 설명부터 잘림), 본문은 호출 때 들어간다. 한 번 들어간 본문은 이후 턴에도 남는다(Q2). 부속 파일은 SKILL.md가 가리킬 때 필요한 만큼 읽힌다. 본문은 500줄 이하를 권한다. 프런트매터(frontmatter, 파일 머리의 설정 블록)는 모두 선택이며 `description`만 권장이고 `disable-model-invocation: true`를 주면 설명이 맥락에 아예 들어가지 않는다. 호출 때만 쓰는 스킬은 이 옵션이 비용을 줄인다.

### [3] Claude Code 슬래시 명령 (스킬로 통합)
- URL: https://code.claude.com/docs/en/slash-commands
- 확인: 2026-11-08 (앞 100,000자만 열람)
- 인용:
  1. "Custom commands have been merged into skills. A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way."
  2. "Prefer a skill for new work, since skills also support supporting files."
- 해석: 사용자 정의 명령은 스킬에 합쳐졌다. 같은 이름이면 스킬이 우선한다. `~/.claude/commands`(전역 위치)를 직접 언급한 문장은 열어 본 구간에서 못 찾았다: [미확인]. 명령 본문도 호출 때만 로드되고 설명만 목록에 있는 것으로 읽히나, 명령 파일에 대한 명시 문장은 없다.

### [4] Claude Code 훅
- URL: https://code.claude.com/docs/en/hooks
- 확인: 2026-11-08 (앞 100,000자만 열람. 나머지 [미확인])
- 인용:
  1. "For most events, Claude Code writes stdout to the debug log and doesn't show it in the transcript. On these events, your stdout reaches Claude or the compaction summary: UserPromptSubmit, UserPromptExpansion, SessionStart, and PostModelSwitch: Claude Code adds plain-text stdout as context that Claude can see and act on. ... PreCompact: your stdout becomes instructions for the compaction summary"
  2. "Exit code 0 with no output means the hook has no decision to report, so the tool call continues through the normal permission flow."
  3. "The `additionalContext` field passes a string from your hook into Claude's context window. Claude Code wraps the string in a system reminder and inserts it into the conversation at the point where the hook fired."
- 해석: 이벤트 종류는 SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, PreCompact, PostCompact, SubagentStart, SessionEnd 등 30여 개다. 출력이 맥락에 들어가는 조건은 위 이벤트의 평문 stdout 또는 JSON의 additionalContext다. 10,000자를 넘으면 파일로 저장하고 2,000자 미리보기만 준다. 출력이 없으면 들어가는 것도 없다(Q4). 단 "토큰 0"이라는 문장은 없다. 훅으로 넣은 맥락은 압축 때 대화와 함께 요약된다. 반면 `compact` 출처에 맞는 SessionStart 훅은 압축 뒤 다시 실행되어 출력이 새로 들어간다([8]).

### [5] Claude Code 비용 관리 (맥락 관리)
- URL: https://code.claude.com/docs/en/costs
- 확인: 2026-11-08 (전문 열람)
- 인용:
  1. "Your CLAUDE.md file is loaded into context at session start. ... Skills load on-demand only when invoked, so moving specialized instructions into skills keeps your base context smaller. Aim to keep CLAUDE.md under 200 lines by including only essentials."
  2. "MCP tool definitions are deferred by default, so only tool names and server instructions enter context until Claude uses a specific tool."
  3. "Clear between tasks: Use `/clear` to start fresh when switching to unrelated work. Stale context wastes tokens on every subsequent message."
- 해석: 항상 들어가는 것(CLAUDE.md)과 필요 때만 들어가는 것(스킬 본문)의 구분을 공식이 권장한다. MCP는 도구 이름과 서버 지시문만 상시 들어간다. `/compact`에 초점 지시를 줄 수 있고 CLAUDE.md에 "Compact instructions" 절을 둘 수 있다. 모든 요청은 전체 대화를 다시 보내므로(캐시 읽기 단가) 맥락이 길수록 매 요청 비용이 커진다. 훅으로 입력을 미리 줄이라는 예시도 있다.

### [6] Claude Code 서브에이전트
- URL: https://code.claude.com/docs/en/sub-agents
- 확인: 2026-11-08 (앞 100,000자만 열람)
- 인용:
  1. "Each subagent runs in its own context window with a custom system prompt, specific tool access, and independent permissions."
  2. "Each subagent starts with a fresh, isolated context window. It doesn't see your conversation history, the skills you've already invoked, or the files Claude has already read."
- 해석: 보조 에이전트(subagent)는 별도 맥락 창에서 일하고 요약만 돌려준다. 시작 때 CLAUDE.md 계층(~/.claude/CLAUDE.md 포함)은 받지만 대화 기록과 이미 호출한 스킬은 못 본다(내장 Explore, Plan은 CLAUDE.md도 건너뜀). 인수인계 파일이 필요한 이유의 공식 근거가 된다.

### [7] Claude Code 상태줄(statusline)
- URL: https://code.claude.com/docs/en/statusline
- 확인: 2026-11-08 (저장본 일부 검색만. 전문 정독 아님)
- 인용:
  1. "The status line is a customizable bar at the bottom of Claude Code that runs any shell script you configure. It receives JSON session data on stdin and displays whatever your script prints"
  2. "The status line runs locally and does not consume API tokens."
- 해석: 상태줄은 로컬 스크립트라 모델 토큰을 쓰지 않고 맥락에도 들어가지 않는다. 맥락 사용률 등을 표시할 수 있어 인수인계 시점 알림용으로 쓸 만하다. 출력이 모델에게 가지 않는다는 점이 훅과 다르다.

### [8] Claude Code 맥락 창 설명 (압축 후 생존표)
- URL: https://code.claude.com/docs/en/context-window
- 확인: 2026-11-08 (전문 열람)
- 인용:
  1. "Before you type anything: CLAUDE.md, auto memory, MCP tool names, and skill descriptions all load into context."
  2. "Invoked skill bodies | Re-injected, capped at 5,000 tokens per skill and 25,000 tokens total; oldest dropped first"
  3. "SessionStart hooks that match the `compact` source | Claude Code runs them and adds their output to the compacted context" / "Context that hooks added earlier | Summarized with the rest of the conversation"
- 해석: 시작 시 상시 로드 목록이 공식 확인된다(Q1). 압축 후 프로젝트 루트 CLAUDE.md와 자동 메모리는 디스크에서 다시 주입되고, 하위 폴더 CLAUDE.md와 path 규칙은 요약에 묻히며 필요 때 다시 로드된다. 압축 뒤 반드시 남겨야 할 지시는 SessionStart(compact) 훅이나 루트 CLAUDE.md에 둔다. `/clear`는 대화를 비우고 비용이 들지 않는다([5]).

### [9] Claude Code MCP
- URL: https://code.claude.com/docs/en/mcp
- 확인: 2026-11-08 (앞 100,000자만 열람. 도구 검색 절은 잘려 [미확인])
- 인용: "With tool search enabled, when a server finishes connecting while Claude is working, Claude Code lists the server's tool names to Claude on its next request in the same turn."
- 해석: 도구 검색(tool search)이 기본이라 이름만 상시 노출된다. 상세 규칙은 [5]의 문장으로 대신하고 이 페이지 해당 절은 확인하지 못했다.

### [10] Anthropic 엔지니어링 글: Effective context engineering for AI agents
- URL: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- 확인: 2026-11-08 (요약 응답을 통해 열람. 문장은 응답에 나온 조각 그대로)
- 인용:
  1. "Context, therefore, must be treated as a finite resource with diminishing marginal returns."
  2. "Structured note-taking, or agentic memory, is a technique where the agent regularly writes notes."
  3. "Sub-agent architectures provide another way around context limitations."
- 해석: 맥락은 유한한 주의 예산이고 늘어날수록 회상 정확도가 떨어진다(맥락 부패, context rot). 대책은 필요 때 불러오기(just-in-time), 점진적 공개, 압축, 외부 노트 기록, 보조 에이전트다. .relay(파일 기반 인수인계)는 구조화된 노트 기록 기법에 해당한다.

### [11] Anthropic 엔지니어링 글: Agent Skills 소개
- URL: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "pre-loads the name and description of every installed skill into its system prompt."
  2. "it will load the skill by reading its full SKILL.md into context."
  3. "These additional linked files are the third level (and beyond) of detail."
- 해석: 1단계 이름·설명, 2단계 SKILL.md 본문, 3단계 연결 파일의 점진적 공개 구조다. 스킬을 얇은 SKILL.md와 부속 파일로 쪼개면 호출 전 비용이 거의 없다.

### [12] Codex AGENTS.md 가이드
- URL: https://developers.openai.com/codex/guides/agents-md (308 이동 후 https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- 확인: 2026-11-08 (이동한 주소에서 열람. 요약 응답 경유)
- 인용:
  1. "Codex reads `AGENTS.override.md` if it exists. Otherwise, Codex reads `AGENTS.md`." (전역은 Codex 홈, 기본 `~/.codex`)
  2. "Starting at the project root (typically the Git root), Codex walks down to your current working directory." / "Files closer to your current directory override earlier guidance because they appear later in the combined prompt."
  3. "Codex builds an instruction chain when it starts (once per run; in the TUI this usually means once per launched session)."
- 해석: 전역, 프로젝트 루트에서 작업 폴더까지 순서로 병합하고 폴더마다 한 파일만 쓴다. 기본 크기 한도는 32 KiB(`project_doc_max_bytes`), 대체 파일명은 `project_doc_fallback_filenames`로 지정한다. 작업 폴더 아래 하위 폴더 파일은 읽지 않는다(Claude Code와 반대). 시작 때 한 번 만든다.

### [13] Codex 스킬
- URL: https://developers.openai.com/codex/skills (이동 후 https://learn.chatgpt.com/docs/build-skills)
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "start with each skill's name and description, then load the full `SKILL.md` instructions"
  2. "By default, this list uses at most 2% of the model's context window"
  3. "Codex scans `.agents/skills` in every directory from your current working directory up to the repository root"
- 해석: 구조는 Claude Code와 같다. 초기 목록에 파일 경로도 들어가고 예산은 맥락 창의 2%다. 전역 위치는 `$HOME/.agents/skills`다. 사용자 정의 프롬프트(custom prompts) 기능은 이 페이지에 없어 [미확인].

### [14] Codex config.toml 및 훅
- URL: https://developers.openai.com/codex/config-basic (이동 후 https://learn.chatgpt.com/docs/config-file/config-basic), 훅: https://learn.chatgpt.com/docs/hooks
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "Codex stores user-level configuration at `~/.codex/config.toml`."
  2. UserPromptSubmit/SessionStart: "Plain text on `stdout` is added as extra developer context." / PreToolUse: "Plain text on `stdout` is ignored."
- 해석: 사용자 설정은 `~/.codex/config.toml`, 프로젝트 설정은 `.codex/config.toml`이다. 훅 출력이 맥락에 들어가는 이벤트가 Claude Code와 같은 방식으로 제한된다. `mcp_servers` 설정 문법은 열어 본 페이지에 없어 [미확인](별도 MCP 페이지로 링크만 있음).

### [15] Antigravity 규칙(rules)
- URL: https://antigravity.google/docs/rules (`/docs/rules-workflows`는 이 주소로 이동)
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "`always_on`: Antigravity injects the full content into the system prompt on every turn." / "`model_decision`: Antigravity injects only the rule's path and `description` upfront."
  2. "`AGENTS.md` and `GEMINI.md` do not use frontmatter." (전체가 항상 적용)
  3. "Antigravity truncates any single rule file that exceeds 24,000 bytes." / "All active global and `always_on` rules share a 20,000-token budget."
- 해석: 전역 규칙은 `~/.gemini/GEMINI.md`(항상 적용), 작업공간 규칙은 `.agents/rules/*.md`(옛 `.agent/rules`도 지원)다. 적용 모드는 manual, always_on, model_decision, glob 넷이다(CLI는 manual 미지원). 항상 적용 규칙은 파일당 24,000바이트, 합계 20,000토큰 예산이 있다. 워크플로(workflows)는 이 페이지에 정의가 없고 스킬 페이지에도 없어 [미확인]. 공식 문서는 존재한다.

### [16] Antigravity 스킬
- URL: https://antigravity.google/docs/skills
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "On every turn, the agent sees a list of available skills with their names and descriptions."
  2. "If a skill looks relevant to your task, the agent reads the full `SKILL.md` content."
  3. "Antigravity defaults to `.agents/skills`, but still maintains backward compatibility for `.agent/skills`."
- 해석: 같은 점진적 공개다. 작업공간은 `.agents/skills/`, 전역은 `~/.gemini/config/skills/`이며 CLI에서는 스킬이 슬래시 명령으로 자동 변환된다. Codex와 Antigravity는 작업공간 폴더가 `.agents/skills`로 같다.

### [17] Antigravity MCP
- URL: https://antigravity.google/docs/mcp
- 확인: 2026-11-08 (요약 응답 경유)
- 인용: "The configuration file is located globally at `~/.gemini/config/mcp_config.json`." / "The root object contains an `mcpServers` map where each key is a unique server identifier."
- 해석: 전역은 `~/.gemini/config/mcp_config.json`, 작업공간은 `<workspace>/.agents/mcp_config.json`이다. 서버는 `command`(로컬) 또는 `serverUrl`(원격)로 지정한다.

### [18] AGENTS.md 표준
- URL: https://agents.md
- 확인: 2026-11-08 (요약 응답 경유)
- 인용:
  1. "A simple, open format for guiding coding agents."
  2. "Agents automatically read the nearest file in the directory tree, so the closest one takes precedence."
  3. "Rename existing files to AGENTS.md and create symbolic links for backward compatibility."
- 해석: 도구 간 공통 지시 파일 표준이다. 가장 가까운 파일이 우선하고, 사용자 채팅 지시가 모두에 우선한다. 지원 목록에 Codex, Gemini CLI 등이 있고 Claude Code와 Antigravity는 이 페이지 목록에서 [미확인](두 도구의 지원은 각 도구 문서 [1][15]에서 확인).

---

## 부록: 원문 확인 상태

- 열어 본 페이지 18개(Claude Code 9, Anthropic 블로그 2, Codex 3 묶음(4페이지), Antigravity 3, agents.md 1).
- 요약 응답 경유 출처(WebFetch가 작은 모델로 요약): [10][11][12]~[18]. 인용 문장은 응답에 따옴표로 나온 것을 옮겼으며 원문 대조는 하지 않았다.
- 확인하지 못한 것: Claude 훅 페이지 뒤 190,000자, 스킬·명령 페이지 뒤 일부, MCP 도구 검색 절, `~/.claude/commands` 직접 언급, Codex 사용자 정의 프롬프트와 mcp_servers 문법, Antigravity 워크플로 정의, Codex·Antigravity에서 호출된 스킬 본문의 이후 턴 유지 여부, 전역 지시 파일을 세 도구가 공유하는 공식 방법.
