"""relay CLI 진입점."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from relay import config as cfg
from relay import pack as packmod
from relay import attribution, doctor, events, hooks, pipeline, mcp_server, router, statusline, tasks, usage
from relay.tokens import estimate, human

BASELINE = Path.home() / ".relay" / "baseline.json"


def _copy(text: str) -> bool:
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip.exe"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text, text=True, check=False)
            return True
    return False


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = Path.cwd()
    for path, status in tasks.scaffold(root, force=args.force):
        print(f"  {status:<16} {path}")
    if args.statusline or args.hooks:
        settings = root / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
        if args.statusline:
            data["statusLine"] = {"type": "command", "command": "relay statusline"}
        if args.hooks:
            hooks.install(data)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        added = " + ".join(n for n, on in (("statusline", args.statusline), ("hooks", args.hooks)) if on)
        print(f"  {added:<16} {settings.relative_to(root)}")
    if args.mcp:
        mcp_json = root / ".mcp.json"
        data = json.loads(mcp_json.read_text(encoding="utf-8")) if mcp_json.is_file() else {}
        data.setdefault("mcpServers", {})["relay"] = {"command": "relay", "args": ["mcp", "--profile", "claude"]}
        mcp_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  {'mcp (claude)':<16} .mcp.json   — Codex/Antigravity: relay mcp-config codex|antigravity")
    print("\n다음: relay task new \"작업 제목\"")
    return 0


def cmd_task(args) -> int:
    root = cfg.find_root()
    conf = cfg.load(root)
    if args.action == "new":
        title = " ".join(args.title)
        path = tasks.create(root, title, router.summary(title, conf))
        print(f"생성: {path.relative_to(root)}/brief.md")
        print(f"추천: {router.summary(title, conf)}")
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
    text = built.render(packmod.header(task.name, built))
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


def cmd_mcp(args) -> int:
    if args.cost:
        for name in mcp_server.PROFILES:
            tools = mcp_server.PROFILES[name]["tools"]
            print(f"{name:<9} ~{mcp_server.definition_tokens(name):>4} tok/요청  tools={len(tools)}"
                  f"  write={','.join(mcp_server.PROFILES[name]['write'])}")
        return 0
    root = Path(args.root) if args.root else (Path(os.environ["RELAY_ROOT"]) if os.environ.get("RELAY_ROOT") else None)
    mcp_server.Server(args.profile, root).serve()
    return 0


def cmd_mcp_config(args) -> int:
    profile = args.profile or mcp_server.DEFAULT_PROFILE[args.client]
    root = None if args.no_root else (Path(args.root) if args.root else cfg.find_root())
    print(mcp_server.client_config(args.client, profile, root))
    hints = {
        "claude": "→ 프로젝트의 .mcp.json 에 저장 (또는 relay init --mcp). 확인: claude 실행 후 /mcp",
        "codex": "→ ~/.codex/config.toml 에 추가. 확인: codex 실행 후 /mcp",
        "antigravity": "→ Antigravity 의 MCP 설정(mcp_config.json, '…' 메뉴 → MCP Servers → Manage → raw config)에 병합",
    }
    print(hints[args.client], file=sys.stderr)
    return 0


def cmd_events(args) -> int:
    root = cfg.find_root()
    records = events.read(root)
    if not records:
        print("(이벤트 없음 — MCP 서버를 통해 도구가 호출되면 .relay/events.jsonl 에 쌓입니다)")
        return 0
    print(f"{'client':<20}{'tool':<20}{'calls':>6}{'errors':>7}{'tok out':>9}{'avoided':>9}")
    for (client, tool), row in events.summarize(records).items():
        print(f"{client[:19]:<20}{tool.removeprefix('relay_'):<20}{row['calls']:>6}{row['errors']:>7}"
              f"{human(row['tokens_out']):>9}{human(row['avoided']):>9}")
    print("(avoided: hook 이 막은 Read/출력의 크기 — 막지 않았다면 이후 요청마다 다시 읽혔을 양)")
    if args.tail:
        print()
        for r in records[-args.tail:]:
            mark = "ok " if r.get("ok", True) else "ERR"
            print(f"{r.get('ts', '')} {mark} {r.get('client', '?'):<14} {r.get('tool', ''):<18} {r.get('task') or ''}")
    return 0


def cmd_why(args) -> int:
    report = attribution.analyze(attribution.find_transcript(args.session))
    n, total = report.requests, report.total or 1
    print(f"세션 {report.path.stem[:8]} · 요청 {n}회 · 컨텍스트 재독 합계 {human(report.total)}")
    print(f"\n{'원인':<20}{'비중':>6}{'토큰':>9}")
    cats = report.by_category()
    for cat, cost in cats:
        print(f"{cat:<20}{cost / total:>6.0%}{human(cost):>9}")
    print(f"\n가장 비싼 내용 {args.top}개  (크기 × 이후 다시 읽힌 횟수)")
    for it in report.top(args.top):
        print(f"  req#{it.start:<4} {human(it.tokens):>6} × {it.reads(n):>3}회 = {human(it.cost(n)):>6}"
              f" ({it.cost(n) / total:.1%})  {it.label[:60]}")
    print("\n처방")
    for cat, cost in cats[:4]:
        if cat in attribution.ADVICE and cost / total >= 0.05:
            print(f"  {cost / total:>4.0%}  {attribution.ADVICE[cat]}")
    return 0


def cmd_doctor(args) -> int:
    d = doctor.diagnose(cfg.find_root(), args.days)
    print(f"주간 Claude 요청 수 (최근 {args.days:g}일 기준): {d.weekly_requests}")
    if d.base_tokens:
        print(f"고정 부분(base, 최근 세션 첫 요청 중앙값): ~{human(d.base_median)} tok"
              f"  → 주당 {human(d.weekly_cost(d.base_median))} 재독")
        print(f"  base 10k 를 줄이면 주당 약 {human(d.weekly_cost(10_000))} 절감")
    print("\n[CLAUDE.md 계열 — 매 요청 포함]")
    for path, tok in d.memory_files or [("(없음)", 0)]:
        print(f"  {human(tok):>6} tok  주당 {human(d.weekly_cost(tok)):>6}  {path}")
    print("\n[MCP 서버 — 도구 정의가 매 요청 포함]")
    for scope, name, cmd in d.mcp_servers or [("-", "(설정 없음)", "")]:
        print(f"  {scope:<8} {name:<20} {cmd}")
    print("\n처방")
    print("  1. Claude Code 에서 /context 실행 → 'MCP tools' · 'Memory files' 항목 크기 확인")
    print("  2. 이 프로젝트에서 안 쓰는 MCP 서버: claude mcp remove <name> -s <scope> (또는 /mcp 에서 disable)")
    print("  3. CLAUDE.md 는 '매번 지켜야 할 규칙'만. 참고 문서는 @import 대신 경로만 적어 필요할 때 읽게")
    print("  4. relay MCP 는 claude 프로필만 (relay mcp --cost)")
    return 0


def cmd_run(args) -> int:
    root = cfg.find_root()
    conf = cfg.load(root)
    task = tasks.resolve(root, args.task)
    if args.verify is not None:
        conf["pipeline"]["verify"] = args.verify
    if args.review_max_turns is not None:
        conf["pipeline"]["review_max_turns"] = args.review_max_turns
    if args.review_model:
        conf["pipeline"]["review_model"] = args.review_model
    stages = args.only or [s for s in pipeline.STAGES if s not in (args.skip or [])]
    for name in args.redo or []:
        (task / f"{name}.md").unlink(missing_ok=True)
    if args.dry_run:
        shown = {k: conf["pipeline"][k] for k in
                 ("review_model", "review_effort", "review_max_turns", "review_fallback_model")}
        shown["system_prompt"] = "<system prompt>"   # 실제 값은 길어서 줄이 잘린다

        def one_line(argv: list[str], width: int) -> str:
            return " ".join(argv).replace("\n", " ")[:width]

        for stage in ("explore", "implement", "review"):
            try:
                runners = [pipeline._fill(r, shown) for r in pipeline.installed_runners(conf["runners"][stage])]
                print(f"{stage:<10} {one_line(runners[0], 180)}")
                for fallback in runners[1:]:
                    print(f"{'':<10} ↳ 실패 시 {one_line(fallback, 170)}")
            except pipeline.RunnerMissing as e:
                print(f"{stage:<10} ✗ {e}")
        print(f"{'verify':<10} {conf['pipeline']['verify'] or '(미설정)'}")
        return 0
    print(f"relay run {task.name} — stages: {' → '.join(stages)}")
    report = pipeline.Pipeline(root, task, conf).run(stages, args.max_iterations or conf["pipeline"]["max_iterations"])
    c = report.claude_tokens
    if c:
        ctx = c.get("input_tokens", 0) + c.get("cache_creation_input_tokens", 0) + c.get("cache_read_input_tokens", 0)
        print(f"\nClaude 사용: {c.get('turns', 0)}턴 · 컨텍스트 {human(ctx)} · 출력 {human(c.get('output_tokens', 0))}")
    print(f"결과: {report.verdict or '-'} (iterations {report.iterations}) → {(task / 'run.json').relative_to(root)}")
    return 0 if report.verdict in ("APPROVE", "") else 2


def cmd_lean(args) -> int:
    root = cfg.find_root()
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    argv = pipeline.lean_argv(cfg.load(root), root, extra)
    if args.print:
        print(" ".join(argv))
        return 0
    return pipeline.exec_lean(cfg.load(root), root, extra)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relay", description="ChatGPT · Antigravity · Claude 토큰 효율 워크플로우")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="현재 프로젝트에 relay 프로토콜 파일 설치")
    s.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    s.add_argument("--statusline", action="store_true", help=".claude/settings.local.json 에 statusline 등록")
    s.add_argument("--mcp", action="store_true", help=".mcp.json 에 relay MCP 서버 등록 (Claude Code)")
    s.add_argument("--hooks", action="store_true", help="Claude Code hooks 등록 (컨텍스트 한도 차단, 큰 Read/출력 차단)")
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

    s = sub.add_parser("mcp", help="MCP 서버 실행 (stdio)")
    s.add_argument("--profile", default="full", choices=list(mcp_server.PROFILES))
    s.add_argument("--root", help="프로젝트 루트 (기본: $RELAY_ROOT → 클라이언트 roots → 현재 디렉터리)")
    s.add_argument("--cost", action="store_true", help="프로필별 도구 정의 토큰 비용 출력")
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("mcp-config", help="클라이언트별 MCP 설정 스니펫 출력")
    s.add_argument("client", choices=list(mcp_server.DEFAULT_PROFILE))
    s.add_argument("--profile", choices=list(mcp_server.PROFILES))
    s.add_argument("--root", help="--root 값 (기본: 현재 프로젝트)")
    s.add_argument("--no-root", action="store_true", help="--root 를 넣지 않음 (클라이언트 cwd/roots 사용)")
    s.set_defaults(func=cmd_mcp_config)

    s = sub.add_parser("events", help="MCP 도구 호출 감사 로그 요약")
    s.add_argument("--tail", type=int, default=0)
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("why", help="세션의 캐시 읽기를 원인별로 분해")
    s.add_argument("session", nargs="?", help="세션 id 접두어 (기본: 현재 프로젝트의 최근 세션)")
    s.add_argument("--top", type=int, default=8)
    s.set_defaults(func=cmd_why)

    s = sub.add_parser("doctor", help="고정 부분(MCP·CLAUDE.md) 진단과 주간 절감량")
    s.add_argument("--days", type=float, default=14)
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("hook", help="Claude Code hook 진입점 (stdin JSON)")
    s.add_argument("event", choices=["prompt", "pretool"])
    s.set_defaults(func=lambda a: hooks.main(a.event))

    s = sub.add_parser("run", help="파이프라인: explore(agy) → pack → implement(Codex) → verify → review(Claude lean)")
    s.add_argument("task", nargs="?")
    s.add_argument("--only", nargs="+", choices=pipeline.STAGES, help="이 단계만 실행")
    s.add_argument("--skip", nargs="+", choices=pipeline.STAGES)
    s.add_argument("--redo", nargs="+", choices=["explore", "pack"], help="기존 산출물을 지우고 다시")
    s.add_argument("--verify", help="테스트 명령 (config 의 pipeline.verify 대신)")
    s.add_argument("--max-iterations", type=int)
    s.add_argument("--review-max-turns", type=int, help="리뷰 턴 상한 (비용은 턴 수가 지배한다)")
    s.add_argument("--review-model", help="리뷰 모델 (기본: config 의 pipeline.review_model)")
    s.add_argument("--dry-run", action="store_true", help="어떤 러너가 쓰일지만 출력")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("lean", help="도구를 줄인 대화형 Claude 실행 (base 약 -78%%)")
    s.add_argument("--print", action="store_true", help="실행할 명령만 출력")
    s.add_argument("extra", nargs=argparse.REMAINDER, help="claude 에 그대로 넘길 인자 (-- 뒤에)")
    s.set_defaults(func=cmd_lean)

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
