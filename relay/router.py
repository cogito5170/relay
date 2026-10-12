"""작업 설명 → 어떤 도구에 맡길지 추천하는 규칙 기반 라우터."""

from dataclasses import dataclass


@dataclass
class Route:
    agent: str
    score: int
    reason: str
    matched: list[str]


def route(text: str, config: dict) -> list[Route]:
    lowered = text.lower()
    results = []
    for rule in config["route"]:
        matched = [kw for kw in rule["keywords"] if kw.lower() in lowered]
        if matched:
            results.append(Route(rule["agent"], len(matched), rule["reason"], matched))
    results.sort(key=lambda r: r.score, reverse=True)
    if not results:
        results.append(Route(config["default_agent"], 0, "매칭 규칙 없음 → 기본값", []))
    return results
