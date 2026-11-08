"""Routing checker: which model actually ran each main session and subagent.

Reads Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl and
<session>/subagents/agent-*.jsonl + .meta.json) and counts `message.model`.

    python3 check_routing.py                       # newest session
    python3 check_routing.py --session 6c519b59    # session id prefix
    python3 check_routing.py --expect "Scout=sonnet" --expect "main=opus"
    python3 check_routing.py --logs ~/.claude/projects/<dir>   # other project (default: current directory's)

--expect PATTERN=MODEL: every subagent whose description contains PATTERN
(or the main session, for PATTERN "main") must have run only models whose
name contains MODEL. Exit 1 if any expectation fails or matches nothing.
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path


def project_logs(root):
    """Claude Code keeps transcripts under ~/.claude/projects/<cwd with non-alphanumerics replaced by '-'>."""
    return Path.home() / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path(root).resolve()))


LOGS = project_logs(Path.cwd())


def models_in(path):
    c = collections.Counter()
    for line in path.open(encoding="utf-8"):
        try:
            m = json.loads(line).get("message")
        except json.JSONDecodeError:
            continue
        if isinstance(m, dict) and m.get("model") and m["model"] != "<synthetic>":
            c[m["model"]] += 1
    return c


def pick_session(prefix):
    logs = sorted(LOGS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in logs:
        if p.stem.startswith(prefix or ""):
            return p
    sys.exit(f"no session matching {prefix!r} in {LOGS}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--expect", action="append", default=[])
    ap.add_argument("--logs", default="")
    a = ap.parse_args()
    global LOGS
    if a.logs:
        LOGS = Path(a.logs).expanduser()

    main_log = pick_session(a.session)
    rows = [("main", "-", models_in(main_log))]
    for meta in sorted((main_log.parent / main_log.stem / "subagents").glob("agent-*.meta.json")):
        info = json.loads(meta.read_text())
        log = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        rows.append((info.get("description", "?"), info.get("agentType", "?"), models_in(log) if log.exists() else collections.Counter()))

    print(f"session {main_log.stem}")
    print("| agent | type | models (assistant messages) |\n|---|---|---|")
    for desc, kind, c in rows:
        print(f"| {desc} | {kind} | {', '.join(f'{m} x{n}' for m, n in c.most_common()) or '-'} |")

    failed = False
    for exp in a.expect:
        pat, want = exp.split("=", 1)
        hits = [r for r in rows if (r[0] == "main" if pat == "main" else pat in r[0])]
        if not hits:
            print(f"FAIL {exp}: no agent matches"); failed = True; continue
        for desc, _, c in hits:
            bad = [m for m in c if want not in m]
            ok = bool(c) and not bad
            print(f"{'PASS' if ok else 'FAIL'} {exp}: {desc} ran {sorted(c) or 'nothing'}")
            failed |= not ok
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
