"""Harvest Claude Code session logs, task cards and checker results into the project's relay ledger.

Idempotent: rerun any time (e.g. at /handoff). Sessions are rewritten from the logs;
cards and checker results are appended only when they changed since the last snapshot.

  Run from the project root. Ledger lives in .relay/ledger/ (add .relay/ledger/raw/ to .gitignore).

  ledger/sessions.csv     one row per session: time, prompts, tags, tools, errors, tokens
  ledger/cards.jsonl      card frontmatter snapshots (append on change) -> status history
  ledger/checks.jsonl     exit code and 예/아니오·PASS/FAIL line counts per --check command (append on change)
  ledger/raw/prompts.jsonl  user prompt text (gitignored: may hold private text)

Usage: python3 harvest.py [--check "CMD" ...] [--logs DIR ...]   (--logs must come last)
"""
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
LEDGER = ROOT / ".relay" / "ledger"
LOGS = [Path.home() / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT.resolve()))]
TAG = re.compile(r"\[([^\[\]\n]{1,12})\]")
IDE = re.compile(r"<ide_\w+>.*?</ide_\w+>", flags=re.S)
NOT_PROMPT = ("<local-command", "<command-", "Caveat:", "[Request interrupted", "<system-reminder", "<task-notification")


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def session(path, seen):
    """seen: entries and message ids already counted in an earlier session file."""
    s = {"session": path.stem[:8], "start": None, "end": None, "prompts": 0, "tagged": 0,
         "commands": Counter(), "tags": Counter(), "tools": Counter(), "tool_errors": 0,
         "denied": 0, "ask_user": 0, "agents": 0, "in_tok": 0, "cache_tok": 0, "out_tok": 0,
         "models": Counter()}
    prompts = []
    for line in path.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        key = (d.get("timestamp"), d.get("type"), hashlib.sha1(json.dumps(m.get("content"), sort_keys=True).encode()).hexdigest())
        if d.get("isSidechain") or key in seen:  # resumed/forked sessions copy history under new uuids
            continue
        seen.add(key)
        ts = d.get("timestamp")
        if ts:
            s["start"] = min(s["start"] or ts, ts)
            s["end"] = max(s["end"] or ts, ts)
        c = m.get("content")
        if d.get("type") == "user" and c is not None:
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        if b.get("is_error"):
                            s["tool_errors"] += 1
                            if "doesn't want to proceed" in json.dumps(b.get("content"), ensure_ascii=False):
                                s["denied"] += 1
            t = IDE.sub("", text_of(c)).strip()
            cmd = re.search(r"<command-name>/?([\w:-]+)</command-name>", t)
            if cmd:
                s["commands"][cmd.group(1)] += 1
            elif t and not d.get("isMeta") and not t.startswith(NOT_PROMPT):
                tags = TAG.findall(t[:400])
                s["prompts"] += 1
                s["tagged"] += bool(tags)
                s["tags"].update(tags)
                prompts.append({"session": s["session"], "ts": ts, "tags": tags, "chars": len(t), "text": t})
        elif d.get("type") == "assistant":
            if m.get("model"):
                s["models"][m["model"]] += 1
            u, mid = m.get("usage"), m.get("id")
            if u and mid not in seen:  # streamed blocks repeat the same message id
                seen.add(mid)
                s["in_tok"] += u.get("input_tokens", 0)
                s["cache_tok"] += u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                s["out_tok"] += u.get("output_tokens", 0)
            for b in c if isinstance(c, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    s["tools"][b["name"]] += 1
                    s["ask_user"] += b["name"] == "AskUserQuestion"
                    s["agents"] += b["name"] in ("Agent", "Task")
    return s, prompts


def frontmatter(md):
    m = re.match(r"---\n(.*?)\n---", md, flags=re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, _, v = line.partition(":")
        if k.strip() and not k.startswith(" "):
            out[k.strip()] = re.sub(r"\s+#.*$", "", v).strip()
    return out


def last_snapshot(path, key):
    last = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            r = json.loads(line)
            last[r[key]] = r["hash"]
    return last


def append_changed(path, key, rows):
    last = last_snapshot(path, key)
    new = 0
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            r["hash"] = hashlib.sha1(json.dumps({k: v for k, v in r.items() if k != "ts"}, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()[:12]
            if last.get(r[key]) != r["hash"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                new += 1
    return new


def cards(now):
    rows = []
    for p in sorted((ROOT / "tasks").glob("C-*.md")):
        md = p.read_text(encoding="utf-8")
        fm = frontmatter(md)
        log = md.split("## 결정 · 버린 것 · 다음 · 지뢰", 1)[-1]
        rows.append({"ts": now, "card": fm.get("id", p.stem), "status": fm.get("status"),
                     "needs": fm.get("needs"), "done_when_cmd": fm.get("done_when") != "human",
                     "budget": fm.get("budget"), "log_entries": len(re.findall(r"^- ", log, flags=re.M)),
                     "dropped": len(re.findall(r"dropped|Dropped|버린|버림", log)),
                     **{t: log.count(f"[{t}]") for t in ("catch", "ai-err", "human")}})
    return rows


def checks(now, cmds):
    rows = []
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            rows.append({"ts": now, "check": cmd, "exit": None, "pass": 0, "fail": 0})
            continue
        lines = p.stdout.splitlines()
        rows.append({"ts": now, "check": cmd, "exit": p.returncode,
                     "pass": sum(l.startswith(("예", "PASS")) for l in lines),
                     "fail": sum(l.startswith(("아니오", "FAIL")) for l in lines)})
    return rows


def main(argv):
    logs = [Path(a) for a in argv[argv.index("--logs") + 1:]] if "--logs" in argv else LOGS
    cmds = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--check"]
    (LEDGER / "raw").mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sessions, prompts, seen = [], [], set()
    files = [p for d in logs for p in d.glob("*.jsonl")]
    for p in sorted(files, key=lambda p: p.stat().st_mtime):
        s, pr = session(p, seen)
        if s["start"]:
            sessions.append(s)
            prompts += pr
    sessions.sort(key=lambda s: s["start"])
    cols = ["session", "start", "end", "minutes", "prompts", "tagged", "tags", "commands", "tool_calls",
            "tools", "tool_errors", "denied", "ask_user", "agents", "in_tok", "cache_tok", "out_tok", "models"]
    with (LEDGER / "sessions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for s in sessions:
            mins = (datetime.fromisoformat(s["end"].replace("Z", "+00:00")) -
                    datetime.fromisoformat(s["start"].replace("Z", "+00:00"))).total_seconds() / 60
            w.writerow({**{k: s[k] for k in cols if k in s and not isinstance(s[k], Counter)},
                        "minutes": round(mins), "tool_calls": sum(s["tools"].values()),
                        **{k: json.dumps(dict(s[k].most_common()), ensure_ascii=False)
                           for k in ("tags", "commands", "tools", "models")}})
    with (LEDGER / "raw" / "prompts.jsonl").open("w", encoding="utf-8") as f:
        for p in prompts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    nc = append_changed(LEDGER / "cards.jsonl", "card", cards(now))
    nk = append_changed(LEDGER / "checks.jsonl", "check", checks(now, cmds))
    print(f"sessions {len(sessions)}, prompts {len(prompts)}, card snapshots +{nc}, check snapshots +{nk}")


if __name__ == "__main__":
    main(sys.argv[1:])
