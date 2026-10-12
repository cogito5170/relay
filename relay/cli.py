"""relay CLI 진입점."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from relay import config as cfg
from relay import pack as packmod
from relay import router, statusline, tasks, usage
from relay.tokens import estimate, human

BASELINE = Path.home() / ".relay" / "baseline.json"


def _copy(text: str) -> bool:
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip.exe"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text, text=True, check=False)
            return True
    return False


def _route_summary(text: str, conf: dict) -> str:
    routes = router.route(text, conf)
    return " → ".join(f"{r.agent}" for r in routes[:3]) + f" ({routes[0].reason})"


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = Path.cwd()
    for path, status in tasks.scaffold(root, force=args.force):
        print(f"  {status:<16} {path}")
    if args.statusline:
        settings = root / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
        data["statusLine"] = {"type": "command", "command": "relay statusline"}
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  {'statusline':<16} {settings.relative_to(root)}")
    print("\n다음: relay task new \"작업 제목\"")
    return 0


def cmd_task(args) -> int:
    root = cfg.find_root()
    conf = cfg.load(root)
    if args.action == "new":
        title = " ".join(args.title)
        path = tasks.create(root, title, _route_summary(title, conf))
        print(f"생성: {path.relative_to(root)}/brief.md")
        print(f"추천: {_route_summary(title, conf)}")
        print(f"brief 다듬기: relay handoff {path.name.split('-')[0]} --to chatgpt --copy")
        return 0
    for t in tasks.list_tasks(root):
        files = sorted(p.name for p in t.iterdir() if p.is_file())
        print(f"{t.name:<40} {' '.join(files)}")
    return 0


def cmd_route(args) -> int:
    for r in router.route(" ".join(args.text), cfg.load()):
        hint = f" [{', '.join(r.matched)}]" if r.matched else ""
        print(f"{r.agent:<12} score={r.score} {r.reason}{hint}")
    return 0


def cmd_pack(args) -> int:
    root = cfg.find_root()
    conf = cfg.load(root)
    task = tasks.resolve(root, args.task)
    refs_text = ""
    for ref_file in args.refs or []:
        p = Path(ref_file)
        p = p if p.is_file() else task / ref_file
        refs_text += p.read_text(encoding="utf-8") + "\n"
    built = packmod.build(root, refs_text=refs_text, full=args.full or [], outlines=args.outline or [],
                          diff_ref=args.diff, include_tree=args.tree,
                          budget=args.budget or conf["budget"]["pack_tokens"])
    header = f"<!-- relay pack: {task.name} · ~{built.tokens} tokens -->\n"
    text = built.render(header)
    if args.stdout:
        print(text)
    else:
        (task / "pack.md").write_text(text, encoding="utf-8")
        print(f"pack.md: {len(built.sections)} sections, ~{human(built.tokens)} tokens"
              f" → {(task / 'pack.md').relative_to(root)}")
    for s in built.skipped:
        print(f"  제외: {s}", file=sys.stderr)
    return 0


def cmd_handoff(args) -> int:
    root = cfg.find_root()
    task = tasks.resolve(root, args.task)
    prompt = tasks.handoff(root, task, args.to, " ".join(args.ask or []))
    text = prompt.read_text(encoding="utf-8")
    if args.print:
        print(text)
        return 0
    print(f"{prompt.relative_to(root)} (~{human(estimate(text))} tokens)")
    if args.copy:
        print("클립보드에 복사됨" if _copy(text) else "클립보드 도구 없음 — 파일을 직접 여세요")
    hints = {
        "claude": f'claude "$(cat {prompt.relative_to(root)})"   # 새 세션의 첫 메시지로',
        "codex": f'codex "$(cat {prompt.relative_to(root)})"',
        "antigravity": "Antigravity 에이전트 패널에 붙여넣기 (또는 /relay-explore)",
        "chatgpt": "ChatGPT 에 붙여넣고, 답변을 out-chatgpt.md 또는 brief.md 에 저장",
    }
    print(f"→ {hints[args.to]}")
    return 0


def _print_table(rows: list[tuple[str, usage.Totals]], weights: dict) -> None:
    head = f"{'':<22}{'req':>6}{'input':>9}{'c.write':>9}{'c.read':>9}{'output':>9}{'read%':>7}{'avg ctx':>9}{'weighted':>10}"
    print(head)
    for label, t in rows:
        print(f"{label[:21]:<22}{t.requests:>6}{human(t.input):>9}{human(t.cache_write):>9}"
              f"{human(t.cache_read):>9}{human(t.output):>9}{t.cache_read_share:>7.0%}"
              f"{human(t.avg_context):>9}{human(t.weighted(weights)):>10}")


def _per_active_day(days: dict[str, usage.Totals], weights: dict) -> dict:
    n = max(1, len(days))
    total = usage.Totals()
    for t in days.values():
        total.merge(t)
    return {"active_days": len(days), "weighted": total.weighted(weights) / n,
            "cache_read": total.cache_read / n, "requests": total.requests / n,
            "avg_context": total.avg_context}


def cmd_usage(args) -> int:
    conf = cfg.load()
    weights = conf["weights"]
    since = usage.since_iso(args.days)
    sources = {"claude": usage.scan_claude(since, args.project or "")}
    if args.source in ("codex", "all"):
        sources["codex"] = usage.scan_codex(since)
    if args.source == "codex":
        sources.pop("claude")

    report = {}
    for name, sessions in sources.items():
        days = usage.by_day(sessions)
        report[name] = {"total": usage.total_of(sessions).as_dict(),
                        "per_active_day": _per_active_day(days, weights)}
        if args.json:
            continue
        print(f"\n== {name} · 최근 {args.days:g}일 · 세션 {len(sessions)}개 ==")
        if not sessions:
            print("(로그 없음)")
            continue
        _print_table(list(days.items()) + [("TOTAL", usage.total_of(sessions))], weights)
        if args.sessions:
            print(f"\n상위 {args.sessions}개 세션 (cache read 기준) — 길고 무거운 세션이 주범")
            top = sorted(sessions.items(), key=lambda kv: kv[1].cache_read, reverse=True)[: args.sessions]
            _print_table([(sid[:21], t) for sid, t in top], weights)

    claude_day = report.get("claude", {}).get("per_active_day")
    if args.save_baseline and claude_day:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"since": since, **claude_day}, indent=2), encoding="utf-8")
        print(f"\n기준선 저장: {BASELINE}")
    elif claude_day and BASELINE.is_file() and not args.json:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        print("\n== 기준선 대비 (활동일 평균, 클수록 좋음) ==")
        for key in ("weighted", "cache_read", "requests", "avg_context"):
            cur = claude_day[key]
            ratio = base[key] / cur if cur else float("inf")
            print(f"  {key:<12} {human(base[key]):>8} → {human(cur):>8}   x{ratio:.1f}")
        print("  목표: weighted x5.0 이상 (Max 5x → Pro)")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_estimate(args) -> int:
    before = usage.plan_cost(args.requests, args.sessions, args.base, args.growth)
    kept = round(args.requests * (1 - args.offload))
    after_base = args.after_base or args.base
    after = usage.plan_cost(kept, args.after_sessions or args.sessions, after_base, args.growth)
    print(f"현재 : 요청 {args.requests}회 / 세션 {args.sessions}개 / base {human(args.base)} → 컨텍스트 재독 {human(before)}")
    print(f"변경 : 요청 {kept}회 (오프로드 {args.offload:.0%}) / 세션 {args.after_sessions or args.sessions}개"
          f" / base {human(after_base)} → {human(after)}")
    print(f"효율 : x{before / after:.1f}" if after else "효율 : ∞")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relay", description="ChatGPT · Antigravity · Claude 토큰 효율 워크플로우")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="현재 프로젝트에 relay 프로토콜 파일 설치")
    s.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    s.add_argument("--statusline", action="store_true", help=".claude/settings.local.json 에 statusline 등록")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("task", help="작업 폴더 생성/목록")
    s.add_argument("action", choices=["new", "list"])
    s.add_argument("title", nargs="*")
    s.set_defaults(func=cmd_task)

    s = sub.add_parser("route", help="작업 설명 → 추천 도구")
    s.add_argument("text", nargs="+")
    s.set_defaults(func=cmd_route)

    s = sub.add_parser("pack", help="다음 에이전트용 최소 컨텍스트(pack.md) 생성")
    s.add_argument("task", nargs="?")
    s.add_argument("--refs", nargs="+", help="path:start-end 참조가 담긴 리포트 (예: explore.md)")
    s.add_argument("--full", nargs="+", help="전체 포함할 파일/글롭")
    s.add_argument("--outline", nargs="+", help="시그니처만 포함할 파일/글롭")
    s.add_argument("--diff", nargs="?", const="HEAD", help="git diff 포함 (기본 HEAD)")
    s.add_argument("--tree", action="store_true", help="파일 트리 포함")
    s.add_argument("--budget", type=int, help="토큰 예산")
    s.add_argument("--stdout", action="store_true")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("handoff", help="에이전트별 인수인계 프롬프트 생성")
    s.add_argument("task", nargs="?")
    s.add_argument("--to", required=True, choices=tasks.AGENTS)
    s.add_argument("--ask", nargs="+", help="이번 단계 요청 (생략 시 에이전트별 기본값)")
    s.add_argument("--copy", action="store_true", help="클립보드로 복사")
    s.add_argument("--print", action="store_true", help="프롬프트를 stdout 으로")
    s.set_defaults(func=cmd_handoff)

    s = sub.add_parser("usage", help="로컬 로그 기반 토큰 사용량 분석")
    s.add_argument("--days", type=float, default=7)
    s.add_argument("--source", choices=["claude", "codex", "all"], default="all")
    s.add_argument("--project", help="Claude 프로젝트 디렉터리 이름 필터 (부분 일치)")
    s.add_argument("--sessions", type=int, default=5, help="상위 N개 세션 표시 (0=생략)")
    s.add_argument("--save-baseline", action="store_true", help="현재 기간을 기준선으로 저장")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_usage)

    s = sub.add_parser("estimate", help="세션 분할·오프로드 효과 계산 (비용 모델)")
    s.add_argument("--requests", type=int, default=100, help="Claude 요청 수 (tool call 포함)")
    s.add_argument("--sessions", type=int, default=1)
    s.add_argument("--base", type=int, default=20_000, help="요청당 고정 컨텍스트 (시스템+도구+CLAUDE.md)")
    s.add_argument("--growth", type=int, default=2_000, help="요청마다 늘어나는 컨텍스트")
    s.add_argument("--offload", type=float, default=0.0, help="다른 도구로 넘기는 요청 비율 0~1")
    s.add_argument("--after-sessions", type=int)
    s.add_argument("--after-base", type=int)
    s.set_defaults(func=cmd_estimate)

    s = sub.add_parser("statusline", help="Claude Code statusline (stdin JSON)")
    s.set_defaults(func=lambda a: statusline.main())
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (LookupError, ValueError, RuntimeError, FileNotFoundError) as e:
        print(f"relay: {e}", file=sys.stderr)
        return 1
