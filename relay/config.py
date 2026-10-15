"""프로젝트 설정(.relay/config.toml) 로딩. 파일이 없으면 기본값을 쓴다."""

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "budget": {
        # statusline 경고 기준: 마지막 요청의 컨텍스트 크기(토큰)
        "warn_context": 40_000,
        "max_context": 60_000,
        # 한 세션에서 이 요청 수를 넘기면 /handoff → /clear 권장
        "warn_requests": 30,
        # relay pack 기본 예산
        "pack_tokens": 12_000,
    },
    # relay hook (Claude Code hooks) — relay init --hooks 로 설치
    "hooks": {
        "enabled": ["prompt_gate", "read_guard", "bash_guard", "write_guard"],  # Claude Pro 기준
        "gate_context": 100_000,  # 최후 방어선. 보통은 lean.compact_window 에서 자동 압축이 먼저 일어난다
        "gate_requests": 100,     # 최후 방어선
        "session_inject": ["startup", "clear", "compact"],  # 이때 brief/state 자동 주입
        "read_max_lines": 400,     # 이보다 긴 파일은 offset/limit 없이 Read 금지
        "read_max_range": 120,     # 한 번에 읽을 수 있는 최대 줄 수 (Read limit, sed -n 'A,Bp', head/tail -n N)
        "write_max_tokens": 6_000, # write_guard 를 켰을 때 한 번에 쓸 수 있는 최대 토큰
        "noisy_commands": ["pytest", "npm test", "npm run test", "yarn test", "pnpm test", "cargo test",
                           "go test", "mvn ", "gradle", "git log", "docker logs", "kubectl logs",
                           "journalctl", "npm install", "pip install", "find /", "ls -R"],
    },
    # 컨텍스트 보관소 (relay/archive.py) — 오래된 대화를 세션 밖으로 옮기고, 비슷한 요청일 때만 요약을 불러온다
    "archive": {
        "enabled": True,
        "trigger_context": 60_000,  # 마지막 요청 컨텍스트가 이 이상이면 transcript 를 보관 (gate_context 보다 먼저)
        "recall": True,             # 프롬프트가 보관된 기록과 겹치면 한 줄 요약을 붙인다
        "recall_min_score": 3,      # 깃발 하나(3점) 이상 맞아야 '비슷한 문맥' 으로 본다
        "recall_max_hits": 3,
        "recall_budget": 400,       # 붙이는 요약의 최대 토큰
        "open_budget": 800,         # relay recall --open 이 돌려주는 원문 발췌의 최대 토큰
        # 도메인(주제) 판정 낱말. 프롬프트에 든 낱말 수로 도메인을 정하고 그 도메인의 기록만 불러온다.
        # 여기 없는 도메인도 config.toml 에 [archive.domains] 로 더하면 된다. 보관이 쌓이면 사용자 말의 깃발로도 배운다.
        "domains": {
            "interior": ["실내", "인테리어", "공간 디자인", "공간디자인", "매장", "플래그십", "도면", "평면도", "청사진",
                         "천장", "벽면", "벽체", "바닥재", "동선", "가구", "젠틀몬스터", "gentle monster", "gentlemonster",
                         "탬버린즈", "interior", "floor plan", "flagship"],
            "fashion": ["의류", "패션", "원단", "봉제", "패턴", "실루엣", "룩북", "컬렉션", "착장", "니트",
                        "셔츠", "자켓", "재킷", "코트", "드레스", "apparel", "garment", "fashion", "fabric"],
            "software": ["코드", "버그", "테스트", "함수", "리포", "커밋", "배포", "api", "pytest", "hook", "refactor",
                         "리팩터", "디버그"],
        },
    },
    # relay run — 토큰 절감 파이프라인
    "pipeline": {
        "max_iterations": 2,        # review 가 CHANGES 면 재구현하는 최대 횟수
        "verify": "",               # 테스트 명령 (예: "python -m pytest -q"). 비우면 건너뜀
        "verify_tail_lines": 40,    # review 에 넘기는 테스트 출력 꼬리 줄 수
        "outline": [],              # pack 에 시그니처로 넣을 글롭
        "base_ref": "HEAD",         # review 할 diff 의 기준
        "review_budget": 15_000,    # review 입력(diff + 새 파일) 최대 토큰
        "review_model": "sonnet",
        "review_effort": "medium",
        # 리뷰 비용은 턴 수가 지배한다 (실측: 3턴 = 가중 15.6k). 너무 낮추면 VERDICT 가 잘린다.
        "review_max_turns": 8,
        # 한도가 소진되면 쓰는 agy 모델 (`agy models` 로 목록 확인). 모델별로 쿼터가 따로다.
        "review_fallback_model": "claude-opus-5-5-medium",
        "implement_fallback_model": "gemini-3.8-flash-high",
        "timeout": 1800,
    },
    # relay watch — Claude 세션을 감시하는 모델. 감시에 Claude 를 쓰지 않는다 (러너에 claude 금지).
    "supervisor": {
        "enabled": True,
        "soft_context": 40_000,     # 마지막 요청 컨텍스트가 이 이상이면 hook 이 판정을 갱신한다
        "max_age": 900,             # 캐시된 판정을 재사용하는 시간(초)
        "timeout": 300,
        "runners": [["codex", "exec", "--sandbox", "read-only", "{prompt}"],
                    ["agy", "--mode", "plan", "--model", "{supervisor_fallback_model}",
                     "--print", "{no_shell}\n\n{prompt}"]],
        "fallback_model": "gemini-3.8-flash-high",
    },
    # 단계별 러너 후보 (앞에서부터 설치된 것 사용). {prompt} 가 없으면 stdin 으로 전달.
    "runners": {
        # agy(Antigravity CLI) 는 plan 모드에서 파일을 읽기만 한다. --print 는 프롬프트를 바로 뒤에 받아야 한다.
        "explore": [["agy", "--mode", "plan", "--print", "{no_shell}\n\n{prompt}"],
                    ["codex", "exec", "--sandbox", "read-only", "{prompt}"]],
        # codex 쿼터가 끝나면 agy 가 구현한다. accept-edits 는 수정을, --sandbox 는 셸 명령을 자동 승인한다.
        "implement": [["codex", "exec", "--sandbox", "workspace-write", "{prompt}"],
                      ["agy", "--sandbox", "--mode", "accept-edits", "--model", "{implement_fallback_model}",
                       "--print", "{no_shell}\n\n{prompt}"]],
        "review": [["claude", "-p", "{prompt}", "--model", "{review_model}", "--effort", "{review_effort}",
                    "--tools", "Read,Grep,Glob", "--strict-mcp-config", "--system-prompt", "{system_prompt}",
                    "--max-turns", "{review_max_turns}", "--output-format", "json"],
                   # Claude 한도가 소진되면 같은 급 판단을 agy(Antigravity) 쿼터에서 받는다.
                   # agy 에는 --system-prompt 가 없어 프롬프트 앞에 붙인다. plan 모드라 파일은 읽기만 한다.
                   ["agy", "--mode", "plan", "--model", "{review_fallback_model}",
                    "--print", "{system_prompt}\n{no_shell}\n\n{prompt}"],
                   # agy 모델까지 소진되면 마지막으로 OpenAI 쿼터에서 받는다 (읽기 전용).
                   ["codex", "exec", "--sandbox", "read-only", "{system_prompt}\n\n{prompt}"]],
        "advisory_claude": [["claude", "-p", "{prompt}", "--model", "{review_model}",
                             "--effort", "{review_effort}", "--tools", "Read,Grep,Glob",
                             "--strict-mcp-config", "--system-prompt", "{advisory_system_prompt}",
                             "--max-turns", "{review_max_turns}", "--output-format", "json"]],
        "advisory_agy": [["agy", "--mode", "plan", "--print",
                          "{advisory_system_prompt}\n{no_shell}\n\n{prompt}"]],
        # relay image — Codex 가 의미 스펙을 상세 프롬프트로 펼치고 내장 이미지 도구로 그린다 (ChatGPT 로그인).
        # 앞 후보는 이미지 생성 기능 플래그를 켠다. 그 플래그를 모르는 codex 버전이면 뒤 후보로 넘어간다.
        "image": [["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
                   "-c", "features.image_generation=true", "{prompt}"],
                  ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "{prompt}"]],
    },
    # relay image — 이미지 한 장 생성 제한 시간(초)
    "image": {"timeout": 900},
    # relay lean — 도구를 줄인 대화형 Claude (실측 base 35.1k → 7.9k)
    "lean": {"tools": "Read,Edit,Write,Bash,Grep,Glob", "strict_mcp": True, "effort": "",
             # Claude Code 가 이 크기에서 스스로 대화를 압축하고 계속한다 → 사용자가 /clear 할 필요 없음
             "compact_window": "80k"},
    # relay auto — 세션을 자동으로 순환하는 감독자
    "auto": {
        "max_chunks": 6,             # 새 세션 최대 개수
        "chunk_turns": 30,           # 세션 하나의 최대 턴
        "handoff_context": 50_000,   # 이 컨텍스트에서 hook 이 state.md 마무리를 강제
        "model": "sonnet",
        "effort": "medium",
        "permission_mode": "acceptEdits",   # 신뢰하는 저장소에서만 "bypassPermissions"
        "allowed_tools": ["Bash(relay delegate:*)", "Bash(relay pack:*)", "Bash(git status:*)",
                          "Bash(git diff:*)", "Bash(python -m pytest -q:*)"],
        "timeout": 3600,
        "command": ["claude", "-p", "{prompt}", "--model", "{model}", "--effort", "{effort}",
                    "--tools", "{tools}", "--strict-mcp-config", "--max-turns", "{max_turns}",
                    "--permission-mode", "{permission_mode}", "--output-format", "json"],
    },
    # API 가격 비율을 "가중 사용량"의 근사치로 쓴다 (input = 1 기준).
    # 구독 한도가 정확히 이 비율로 계산된다는 공식 문서는 없으므로 raw 값과 함께 본다.
    "weights": {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0},
    "route": [
        {
            "agent": "antigravity",
            "reason": "대용량 컨텍스트 탐색/읽기는 agy(Antigravity) 쿼터로 처리",
            "keywords": ["explore", "find", "where", "understand", "read", "survey", "map",
                         "탐색", "찾아", "어디", "파악", "분석", "구조", "읽어", "조사"],
        },
        {
            "agent": "chatgpt",
            "reason": "구상·스펙·문서·리서치는 대화형 모델로 (Claude 토큰 0)",
            "keywords": ["plan", "spec", "design doc", "brainstorm", "research", "docs", "readme",
                         "기획", "스펙", "명세", "아이디어", "문서", "정리", "리서치", "요구사항"],
        },
        {
            "agent": "codex",
            "reason": "기계적 구현·테스트·보일러플레이트는 Codex/Antigravity 에서",
            "keywords": ["boilerplate", "scaffold", "crud", "test", "rename", "migrate", "format",
                         "보일러", "스캐폴드", "테스트", "반복", "변환", "마이그레이션", "구현"],
        },
        {
            "agent": "claude",
            "reason": "고난도 판단(설계 결정·디버깅·보안·최종 리뷰)에만 Claude 사용",
            "keywords": ["architecture", "debug", "race", "concurrency", "security", "review",
                         "tricky", "root cause", "설계 결정", "아키텍처", "디버그", "디버깅",
                         "동시성", "보안", "리뷰", "원인", "버그"],
        },
    ],
    "default_agent": "chatgpt",
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def find_root(start: Path | None = None) -> Path:
    """`.relay/` 가 있는 가장 가까운 상위 디렉터리. 없으면 현재 디렉터리."""
    start = (start or Path.cwd()).resolve()
    for d in (start, *start.parents):
        if (d / ".relay").is_dir():
            return d
    return start


def load(root: Path | None = None) -> dict:
    path = find_root(root) / ".relay" / "config.toml"
    if not path.is_file():
        return copy.deepcopy(DEFAULTS)
    with path.open("rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))
