"""Handoff checker: format, cause/evidence on blocked items, and stale Waiting items.

    python3 check_handoff.py [.relay/HANDOFF-NEXT.md] [--words 180]

Waiting item forms (one line each):
    - <text> | check: <shell command; exit 0 means the item is resolved>
    - <text> | human                       (only a person can resolve it)
Any line in Waiting or Session that says blocked must also carry
    | cause: <why> | evidence: <error line or command output>

Exit 1 on any FAIL. A Waiting item whose check exits 0 is STALE (already
resolved but still listed) and fails the handoff.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur and line.strip():
            out[cur].append(line.strip())
    return out


def fields(item):
    # fields are separated by " | " so a bare | inside a check command survives
    parts = [p.strip() for p in re.split(r"\s\|\s", item.lstrip("- "))]
    kv = {}
    for p in parts[1:]:
        k, _, v = p.partition(":")
        kv[k.strip().lower()] = v.strip()
    return parts[0], kv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=".relay/HANDOFF-NEXT.md")
    ap.add_argument("--words", type=int, default=180)
    a = ap.parse_args()
    text = Path(a.path).read_text(encoding="utf-8")
    lines = text.splitlines()
    sec = sections(text)
    fails = []

    if not re.fullmatch(r"STATUS: (CONTINUE|DONE)", lines[0] if lines else ""):
        fails.append("first line is not STATUS: CONTINUE|DONE")
    if len(lines) < 2 or not lines[1].startswith("META:"):
        fails.append("second line is not META:")
    for s in ("Waiting", "Uncommitted", "Session"):
        if s not in sec:
            fails.append(f"missing ## {s}")
    if len(text.split()) > a.words:
        fails.append(f"{len(text.split())} words > {a.words}")

    for item in sec.get("Waiting", []):
        head, kv = fields(item)
        if "check" in kv:
            r = subprocess.run(kv["check"], shell=True, capture_output=True)
            if r.returncode == 0:
                fails.append(f"STALE (check passes, item already resolved): {head}")
        elif "human" not in item.lower().split("|")[-1]:
            fails.append(f"Waiting item has neither check: nor human: {head}")
    for item in sec.get("Waiting", []) + sec.get("Session", []):
        head, kv = fields(item)
        weak = {"", "none", "n/a", "-", "unknown"}
        if re.search(r"\bblocked\b", item, re.I) and (kv.get("cause", "").lower() in weak or kv.get("evidence", "").lower() in weak):
            fails.append(f"blocked item without cause: and evidence: {head}")

    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else f"{len(fails)} FAIL")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
