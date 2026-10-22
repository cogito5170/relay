"""PDF 드래그 질문 (`relay ask`).

미리보기 / Chrome PDF 에서 드래그 → 단축키 → 질문 → 브라우저에 설명 페이지.

토큰 분산
- 맥락은 로컬에서 만든다: PDF 전체가 아니라 선택 문장이 있는 쪽을 찾아 주변 문단만 (`pdftotext`, Claude 토큰 0).
- 쉬운 설명은 agy → Codex 순서 (Claude 토큰 0). 깊은 질문(앞에 `!`, 유도·증명·수식 위주 선택)만 Claude 1턴.
  Claude 는 도구 없이(--tools "") 짧은 system prompt 로 실행한다.
- 모든 답은 notes/papers/<문서>/qa.md 에 쌓여 다시 볼 때 토큰이 들지 않는다.
"""

import html
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from relay import events
from relay.pipeline import NO_SHELL_NOTE, _fill, parse_claude_json

DEEP_HINTS = ("유도", "증명", "도출", "엄밀", "왜 그런", "derive", "derivation", "proof", "prove")
MATH_CHARS = set("∑∫∂∇≈≤≥≠±×÷√∞αβγδεθλμσφψωΔΣΩ=^_\\")

SYSTEM_PROMPT = ("You are a patient university tutor. Explain in Korean, accurately and simply. "
                 "Use LaTeX ($...$, $$...$$) for math and a ```mermaid block only when a diagram truly helps.")

EXPLAIN_PROMPT = """학습자가 PDF 교안/논문에서 아래 부분을 드래그해 질문했다. 한국어로 쉽고 정확하게 설명하라.

# 질문
{question}

# 드래그한 부분
{selection}

# 주변 맥락 ({where})
{context}

# 답변 형식 (마크다운, 600~900단어 이내)
1. **한 줄 요약**
2. **쉬운 설명** — 비유나 직관을 먼저, 그다음 정확한 의미. 전공 용어는 처음 나올 때 풀어서.
3. **핵심 수식** — 필요할 때만. LaTeX 로 쓰고($$...$$), 각 기호의 뜻과 단위를 표로 설명. 유도가 필요하면 단계별로.
4. **그림** — 구조·흐름·관계를 보여주면 이해가 쉬울 때만 ```mermaid 블록 하나.
5. **확인 질문** — 이해했는지 스스로 점검할 질문 1개 (답은 접어서 마지막 줄에).
- 맥락에 없는 내용을 추측할 때는 "추정"이라고 밝혀라. 과장하지 마라.
"""


MODE_PROMPTS = {
    "explain": "한 줄 요약 → 직관/비유 → 정확한 의미 → 필요시 수식과 기호 표 → 필요시 mermaid 그림 1개 → 확인 질문 1개.",
    "research": '이 개념이 나온 맥락과 대표 연구 흐름 2~4개를 "분야/방법/핵심 아이디어"로 설명. 논문 제목·저자·연도는 확실할 때만 쓰고 아니면 "확인 필요"로 표시. DOI·링크를 지어내지 않는다. 각 항목 끝에 검색어: 검색할 문구 형식의 독립된 줄을 반드시 쓴다.',
    "learn": "선수 지식 1~3개, 다음에 공부할 것 1~3개, 흔한 오해 1개를 설명.",
    "deep": "깊게 설명하고 유도·증명은 단계별로. 한 줄 요약, 직관, 정확한 의미, 필요한 수식과 기호 표, 확인 질문 1개를 포함.",
}


def professor_prompt(mode: str, **values) -> str:
    if mode not in MODE_PROMPTS:
        raise ValueError("알 수 없는 교수 모드")
    # Research/learning have their own structure rather than the default outline.
    return EXPLAIN_PROMPT.split("# 답변 형식")[0].format(**values) + "# 답변 형식\n" + MODE_PROMPTS[mode]


def search_links(markdown: str) -> list[dict]:
    """Build search URLs locally; never treat generated URLs as citations."""
    queries = re.findall(r"(?m)^\s*(?:[-*]\s*)?(?:\*\*)?검색어(?:\*\*)?\s*[:：]\s*(.+)$", markdown)
    return [{"query": q.strip(),
             "google_scholar": "https://scholar.google.com/scholar?q=" + urllib.parse.quote(q.strip()),
             "semantic_scholar": "https://www.semanticscholar.org/search?q=" + urllib.parse.quote(q.strip())}
            for q in dict.fromkeys(queries) if q.strip()]


# ---------------------------------------------------------------- 맥락 (로컬, 토큰 0)

def resolve_source(source: str) -> Path | None:
    """파일 경로 · file:// · http(s):// (Chrome 에서 연 웹 PDF 는 ~/.relay/pdf-cache 로 내려받는다)."""
    if not source:
        return None
    if source.startswith("file://"):
        path = Path(urllib.parse.unquote(urllib.parse.urlparse(source).path))
    elif source.startswith(("http://", "https://")):
        cache = Path.home() / ".relay" / "pdf-cache"
        cache.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.-]", "_", Path(urllib.parse.urlparse(source).path).name or "document.pdf")
        path = cache / name
        if not path.is_file():
            try:
                urllib.request.urlretrieve(source, path)
            except OSError:
                return None
    else:
        path = Path(source).expanduser()
    return path if path.is_file() else None


def pdf_pages(pdf: Path) -> list[str]:
    if not shutil.which("pdftotext"):
        return []
    proc = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120)
    return proc.stdout.split("\f") if proc.returncode == 0 else []


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def locate(pages: list[str], selection: str) -> int | None:
    """선택 문장이 있는 쪽 (1부터). 정확히 못 찾으면 단어가 가장 많이 겹치는 쪽."""
    key = _norm(selection)[:80]
    if not key:
        return None
    for i, page in enumerate(pages):
        if key in _norm(page):
            return i + 1
    words = set(key.split())
    best_score, best_page = 0, None
    for i, page in enumerate(pages):
        score = len(words & set(_norm(page).split()))
        if score > best_score:
            best_score, best_page = score, i + 1
    return best_page if best_score >= max(3, len(words) // 2) else None


def context_window(pages: list[str], page: int, selection: str, chars: int) -> str:
    text = re.sub(r"\s+", " ", pages[page - 1]).strip()
    i = text.lower().find(_norm(selection)[:60])
    if i < 0:
        return text[:chars]
    start = max(0, i - chars // 2)
    return text[start:start + chars]


def wants_deep(question: str, selection: str) -> bool:
    if any(h in question.lower() for h in DEEP_HINTS):
        return True
    math = sum(1 for c in selection if c in MATH_CHARS)
    return len(selection) > 0 and math / len(selection) > 0.08


# ---------------------------------------------------------------- 실행

def _run(template: list[str], prompt: str, values: dict, cwd: Path, timeout: int) -> tuple[int, str, dict]:
    uses_arg = any("{prompt}" in a for a in template)
    argv = _fill(template, {**values, "prompt": prompt})
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          input=None if uses_arg else prompt, stdin=subprocess.DEVNULL if uses_arg else None)
    if template[0] == "claude":
        text, usage = parse_claude_json(proc.stdout)
        return proc.returncode, text, usage
    return proc.returncode, proc.stdout.strip(), {}


def answer(conf: dict, prompt: str, deep: bool, cwd: Path) -> tuple[str, str, dict, list[str]]:
    """설치된 러너를 순서대로 시도한다. (답, 러너, Claude 사용량, 실패 기록)"""
    a = conf["ask"]
    candidates = a["deep"] + a["easy"] if deep else a["easy"] + a["deep"]
    values = {"no_shell": NO_SHELL_NOTE, "system_prompt": SYSTEM_PROMPT, "model": a["model"]}
    failures = []
    for template in candidates:
        if not template:
            continue
        if not shutil.which(template[0]):
            failures.append(f"{template[0]}: not installed")
            continue
        try:
            rc, text, usage = _run(template, prompt, values, cwd, a["timeout"])
        except OSError as exc:
            failures.append(f"{template[0]}: {exc}")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"{template[0]}: timeout")
            continue
        if rc == 0 and text:
            return text, template[0], usage, failures
        failures.append(f"{template[0]}: exit {rc}")
    raise RuntimeError("응답한 러너가 없습니다 (" + ", ".join(failures or ["설치된 러너 없음"]) + ")")


def slug(name: str) -> str:
    return re.sub(r"[^\w가-힣.-]+", "-", name).strip("-")[:60] or "selection"


def render_html(meta: dict, markdown: str) -> str:
    data = json.dumps({"answer": markdown}, ensure_ascii=False).replace("</", "<\\/")
    e = {k: html.escape(str(v)) for k, v in meta.items()}
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>relay ask</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<style>
:root {{ --bg:#fff; --fg:#1f2328; --muted:#656d76; --card:#f6f8fa; --line:#d0d7de; --accent:#0969da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --card:#161b22; --line:#30363d; --accent:#4493f8; }} }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.7 -apple-system, "Apple SD Gothic Neo", sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:820px; margin:0 auto; }}
.meta {{ color:var(--muted); font-size:13px; }}
blockquote.sel {{ background:var(--card); border-left:4px solid var(--accent); margin:12px 0 24px; padding:12px 16px; white-space:pre-wrap; }}
pre, code {{ background:var(--card); border-radius:6px; }} pre {{ padding:12px; overflow-x:auto; }}
table {{ border-collapse:collapse; }} td, th {{ border:1px solid var(--line); padding:4px 10px; }}
.mermaid {{ background:#fff; border-radius:8px; padding:8px; }}
</style></head><body><main>
<p class="meta">{e['doc']} · {e['where']} · 답변: {e['runner']} · {e['time']}</p>
<h1>{e['question']}</h1>
<blockquote class="sel">{e['selection']}</blockquote>
<article id="answer"></article>
</main>
<script>
const DATA = {data};
const math = [];
const src = DATA.answer.replace(/\\$\\$([\\s\\S]+?)\\$\\$|\\$([^$\\n]+?)\\$/g, m => {{ math.push(m); return "@@MATH" + (math.length - 1) + "@@"; }});
let out = (window.marked ? marked.parse(src) : src.replace(/[&<]/g, c => c === "&" ? "&amp;" : "&lt;"));
out = out.replace(/@@MATH(\\d+)@@/g, (_, i) => math[+i].replace(/&/g, "&amp;").replace(/</g, "&lt;"));
const el = document.getElementById("answer");
el.innerHTML = out;
if (window.renderMathInElement) renderMathInElement(el, {{ delimiters: [{{left: "$$", right: "$$", display: true}}, {{left: "$", right: "$", display: false}}] }});
el.querySelectorAll("code.language-mermaid").forEach(c => {{ const d = document.createElement("div"); d.className = "mermaid"; d.textContent = c.textContent; c.parentElement.replaceWith(d); }});
if (window.mermaid) {{ mermaid.initialize({{ startOnLoad: false }}); mermaid.run(); }}
</script></body></html>
"""


def ask(root: Path, conf: dict, selection: str, question: str, source: str = "", deep: bool = False,
        open_page: bool = False, mode: str | None = None, page: int | None = None,
        pages: list[str] | None = None) -> dict:
    selection = selection.strip()
    if not selection:
        raise ValueError("선택한 텍스트가 없습니다")
    question = question.strip() or "쉽게 설명해줘"
    pdf = resolve_source(source)
    pages = pages if pages is not None else (pdf_pages(pdf) if pdf else [])
    if page is None:
        page = locate(pages, selection) if pages else None
    elif type(page) is not int or page < 1:
        raise ValueError("쪽 번호는 양의 정수여야 합니다")
    context = context_window(pages, page, selection, conf["ask"]["context_chars"]) if page and page <= len(pages) else "(맥락 없음 — 선택 부분만)"
    where = f"{pdf.name} p.{page}" if pdf and page else (pdf.name if pdf else "출처 미상")
    deep = (mode == "deep") if mode else (deep or wants_deep(question, selection))

    started = time.monotonic()
    values = dict(question=question, selection=selection, where=where, context=context)
    prompt = professor_prompt(mode, **values) if mode else EXPLAIN_PROMPT.format(**values)
    text, runner, usage, failures = answer(conf, prompt, deep, root)
    links = (search_links(text) or search_links("검색어: " + _norm(selection)[:200])) if mode == "research" else []
    for link in links:
        text += f"\n\n검색: [{link['query']} — Google Scholar]({link['google_scholar']}) · [Semantic Scholar]({link['semantic_scholar']})"

    doc_dir = root / conf["ask"]["notes"] / slug(pdf.stem if pdf else "selection")
    (doc_dir / "answers").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    meta = {"doc": pdf.name if pdf else "선택 텍스트", "where": where, "runner": runner,
            "time": stamp.strftime("%Y-%m-%d %H:%M"), "question": question, "selection": selection}
    page_file = doc_dir / "answers" / f"{stamp:%Y%m%d-%H%M%S-%f}.html"
    page_file.write_text(render_html(meta, text), encoding="utf-8")
    with (doc_dir / "qa.md").open("a", encoding="utf-8") as f:
        quoted = "\n".join("> " + line for line in selection.splitlines())
        f.write(f"\n## {question} ({where}, {runner}, {meta['time']})\n\n{quoted}\n\n{text}\n")

    events.append(root, client="ask", profile="deep" if deep else "easy", tool=runner, task=where[:80], ok=True,
                  tokens_out=0, seconds=round(time.monotonic() - started, 1), failures=failures)
    if open_page:
        webbrowser.open(page_file.as_uri())
    return {"runner": runner, "deep": deep, "page": page, "html": page_file, "notes": doc_dir / "qa.md",
            "usage": usage, "failures": failures, "markdown": text, "links": links}


# ---------------------------------------------------------------- macOS 빠른 동작 설치

QUICK_ACTION = r"""#!/bin/bash
# relay ask — macOS 빠른 동작(단축어)에서 선택한 텍스트를 받아 질문한다.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "__ROOT__" || exit 1
sel="$(cat)"
app="$(osascript -e 'tell application "System Events" to name of first application process whose frontmost is true' 2>/dev/null)"
src=""
case "$app" in
  Preview) src="$(osascript -e 'tell application "Preview" to get path of front document' 2>/dev/null)" ;;
  "Google Chrome") src="$(osascript -e 'tell application "Google Chrome" to get URL of active tab of front window' 2>/dev/null)" ;;
esac
q="$(osascript -e 'text returned of (display dialog "무엇이 궁금한가요? (깊게 물으려면 앞에 !)" default answer "쉽게 설명해줘" with title "relay ask")' 2>/dev/null)" || exit 0
deep=""
case "$q" in "!"*) deep="--deep"; q="${q#!}" ;; esac
osascript -e 'display notification "답변을 준비하고 있어요" with title "relay ask"' >/dev/null 2>&1
printf '%s' "$sel" | relay ask --source "$src" --question "$q" $deep --open >"$HOME/.relay/ask.log" 2>&1 &
"""


def install_quick_action(root: Path) -> Path:
    target = Path.home() / ".local" / "bin" / "relay-ask-selection"
    target.parent.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".relay").mkdir(exist_ok=True)
    target.write_text(QUICK_ACTION.replace("__ROOT__", str(root.resolve())), encoding="utf-8")
    target.chmod(0o755)
    return target
