"""프로젝트 설정(.relay/config.toml) 로딩. 파일이 없으면 기본값을 쓴다."""

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "budget": {
        # statusline 경고 기준: 마지막 요청의 컨텍스트 크기(토큰)
        "warn_context": 60_000,
        "max_context": 100_000,
        # 한 세션에서 이 요청 수를 넘기면 /handoff → /clear 권장
        "warn_requests": 40,
        # relay pack 기본 예산
        "pack_tokens": 12_000,
    },
    # API 가격 비율을 "가중 사용량"의 근사치로 쓴다 (input = 1 기준).
    # 구독 한도가 정확히 이 비율로 계산된다는 공식 문서는 없으므로 raw 값과 함께 본다.
    "weights": {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0},
    "route": [
        {
            "agent": "antigravity",
            "reason": "대용량 컨텍스트 탐색/읽기는 Gemini 쪽 쿼터로 처리",
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
