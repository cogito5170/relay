"""relay MCP 서버 (stdio, JSON-RPC 2.0, 표준 라이브러리만 사용).

Claude Code · Codex CLI · Antigravity 가 `.relay/tasks/` 를 복붙 없이 도구 호출로 읽고 쓰게 한다.

설계 원칙
- **도구 정의도 토큰이다.** MCP 도구 스키마는 매 요청 base 컨텍스트에 들어간다. 그래서 설명은 짧은 영어로,
  프로필별로 필요한 도구만 노출한다 (`relay mcp --cost` 로 프로필별 비용 확인).
- **최소 권한.** 프로필마다 쓸 수 있는 task 파일이 다르다 (explorer 는 explore.md, builder 는 out-codex.md …).
  읽기는 프로젝트 루트 밖으로 나갈 수 없다.
- **관측.** 모든 호출을 `.relay/events.jsonl` 에 남긴다 (`relay events`).
"""

import fnmatch
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from relay import __version__
from relay import config as cfg
from relay import events, router, tasks, usage
from relay import pack as packmod
from relay.tokens import estimate, human

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
ROOTS_REQUEST_ID = "relay-roots"
MAX_WRITE_CHARS = 200_000
FILE_RE = re.compile(r"^(brief|state|explore|pack|out-[a-z]+|prompt-[a-z]+)$")

INSTRUCTIONS = ("Relay tasks live in .relay/tasks/<id>/. Start with relay_task_read; "
                "save progress with relay_task_write(file='state').")

_TASK = {"type": "string", "description": "task id or number; omit for latest"}
_STRS = {"type": "array", "items": {"type": "string"}}

TOOLS = {
    "relay_task_list": {
        "description": "List relay tasks and their files.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "relay_task_read": {
        "description": "Read task files (default: brief, state, pack).",
        "inputSchema": {"type": "object", "properties": {
            "task": _TASK,
            "files": {**_STRS, "description": "brief|state|pack|explore|out-<agent>"},
        }},
    },
    "relay_task_write": {
        "description": "Write a task file. Allowed names depend on server profile.",
        "inputSchema": {"type": "object", "properties": {
            "task": _TASK,
            "file": {"type": "string", "description": "e.g. state, explore, out-codex"},
            "content": {"type": "string"},
            "append": {"type": "boolean"},
        }, "required": ["file", "content"]},
    },
    "relay_task_create": {
        "description": "Create a task. Returns id and suggested agent route.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"},
            "brief": {"type": "string", "description": "markdown: Goal/Constraints/Done when"},
        }, "required": ["title"]},
    },
    "relay_pack": {
        "description": "Build the task's pack.md within a token budget: line excerpts from "
                       "path:start-end refs, full files, signature outlines, git diff.",
        "inputSchema": {"type": "object", "properties": {
            "task": _TASK,
            "refs": {"type": "string", "description": "task file holding refs, e.g. explore"},
            "full": {**_STRS, "description": "paths/globs"},
            "outline": {**_STRS, "description": "paths/globs"},
            "diff": {"type": "string", "description": "git ref"},
            "budget": {"type": "integer"},
            "return_content": {"type": "boolean"},
        }},
    },
    "relay_route": {
        "description": "Suggest which agent should handle a task description.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    "relay_handoff": {
        "description": "Render a paste-ready prompt file for an agent without MCP (e.g. ChatGPT web).",
        "inputSchema": {"type": "object", "properties": {
            "task": _TASK,
            "to": {"type": "string", "enum": list(tasks.AGENTS)},
            "ask": {"type": "string"},
        }, "required": ["to"]},
    },
    "relay_usage": {
        "description": "Token usage summary (Claude Code, Codex) from local logs.",
        "inputSchema": {"type": "object", "properties": {"days": {"type": "number"}}},
    },
}

_CORE = ["relay_task_list", "relay_task_read", "relay_task_write"]
PROFILES = {
    # Claude: 판단 담당 — 상태 기록, 필요시 pack 재구성. usage/handoff 는 base 비용 때문에 제외.
    "claude": {"tools": _CORE + ["relay_task_create", "relay_pack", "relay_route"],
               "write": ["brief", "state", "out-claude"]},
    # Antigravity: 탐색 결과를 explore.md 로.
    "explorer": {"tools": _CORE, "write": ["explore", "state", "out-antigravity"]},
    # Codex: 구현 담당.
    "builder": {"tools": _CORE + ["relay_pack"], "write": ["state", "out-codex"]},
    "full": {"tools": list(TOOLS), "write": ["brief", "state", "explore", "out-*"]},
}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class ToolError(Exception):
    pass


def tool_definitions(profile: str) -> list[dict]:
    return [{"name": name, **TOOLS[name]} for name in PROFILES[profile]["tools"]]


def definition_tokens(profile: str) -> int:
    """이 프로필이 Claude 의 매 요청 base 에 더하는 토큰 (근사)."""
    return estimate(json.dumps(tool_definitions(profile), ensure_ascii=False)) + estimate(INSTRUCTIONS)


def _str(args: dict, key: str, required: bool = False) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f"'{key}' is required")
        return ""
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string")
    return value


def _strs(args: dict, key: str) -> list[str]:
    value = args.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not all(isinstance(v, str) for v in value):
        raise ToolError(f"'{key}' must be a list of strings")
    return value


def _file_name(name: str) -> str:
    name = name.removesuffix(".md")
    if not FILE_RE.match(name):
        raise ToolError(f"invalid task file '{name}' (brief|state|pack|explore|out-<agent>)")
    return name


class Server:
    def __init__(self, profile: str = "full", root: Path | None = None, out=None):
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile} ({', '.join(PROFILES)})")
        self.profile_name = profile
        self.profile = PROFILES[profile]
        self.explicit_root = root.resolve() if root else None
        self.roots_root: Path | None = None
        self.client = "unknown"
        self.client_supports_roots = False
        self.out = out or sys.stdout

    # ------------------------------------------------------------ transport

    def send(self, message) -> None:
        self.out.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.out.flush()

    def serve(self, stdin=None) -> None:
        for line in stdin or sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.send(_error(None, -32700, "Parse error"))
                continue
            batch = message if isinstance(message, list) else [message]
            responses = [r for r in map(self.handle, batch) if r is not None]
            if responses:
                self.send(responses if isinstance(message, list) else responses[0])

    def handle(self, message) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid request")
        if "method" not in message:  # 우리가 보낸 요청(roots/list)에 대한 응답
            if message.get("id") == ROOTS_REQUEST_ID and isinstance(message.get("result"), dict):
                self._set_roots(message["result"].get("roots") or [])
            return None

        method, msg_id = message["method"], message.get("id")
        params = message.get("params") or {}
        is_notification = "id" not in message
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_definitions(self.profile_name)}
            elif method == "tools/call":
                result = self._call(params)
            elif method in ("notifications/initialized", "notifications/roots/list_changed"):
                if self.client_supports_roots and not self.explicit_root:
                    self.send({"jsonrpc": "2.0", "id": ROOTS_REQUEST_ID, "method": "roots/list"})
                return None
            elif is_notification:
                return None
            else:
                raise RpcError(-32601, f"Method not found: {method}")
        except RpcError as e:
            return None if is_notification else _error(msg_id, e.code, str(e))
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        self.client = (params.get("clientInfo") or {}).get("name") or "unknown"
        self.client_supports_roots = "roots" in (params.get("capabilities") or {})
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "relay", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _set_roots(self, roots: list[dict]) -> None:
        candidates = []
        for r in roots:
            uri = r.get("uri", "") if isinstance(r, dict) else ""
            if uri.startswith("file://"):
                candidates.append(cfg.find_root(Path(unquote(urlparse(uri).path))))
        with_relay = [c for c in candidates if (c / ".relay").is_dir()]
        if with_relay or candidates:
            self.roots_root = (with_relay or candidates)[0]

    # ------------------------------------------------------------ tools

    @property
    def root(self) -> Path:
        return self.explicit_root or self.roots_root or cfg.find_root()

    def _project(self) -> Path:
        root = self.root
        if not (root / ".relay").is_dir():
            raise ToolError(f"no .relay/ in {root} — run `relay init` there or start the server with --root")
        return root

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in self.profile["tools"]:
            raise RpcError(-32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            raise RpcError(-32602, "arguments must be an object")
        started = time.monotonic()
        try:
            text, ok = getattr(self, "t_" + name.removeprefix("relay_"))(args), True
        except (ToolError, LookupError, ValueError, RuntimeError, OSError) as e:
            text, ok = f"error: {e}", False
        events.append(self.root, client=self.client, profile=self.profile_name, tool=name,
                      task=args.get("task") if isinstance(args.get("task"), str) else None,
                      ok=ok, tokens_out=estimate(text), ms=round((time.monotonic() - started) * 1000))
        return {"content": [{"type": "text", "text": text}], "isError": not ok}

    def _task(self, args: dict) -> tuple[Path, Path]:
        root = self._project()
        return root, tasks.resolve(root, _str(args, "task") or None)

    def t_task_list(self, args: dict) -> str:
        root = self._project()
        rows = [f"{t.name}: {' '.join(sorted(p.stem for p in t.glob('*.md')))}" for t in tasks.list_tasks(root)]
        return "\n".join(rows) or "(no tasks — use relay_task_create)"

    def t_task_read(self, args: dict) -> str:
        _, task = self._task(args)
        names = [_file_name(n) for n in _strs(args, "files")] or ["brief", "state", "pack"]
        parts = [f"task: {task.name}"]
        for name in names:
            path = task / f"{name}.md"
            body = path.read_text(encoding="utf-8").strip() if path.is_file() else "(none)"
            parts.append(f"## {name}.md\n{body}")
        return "\n\n".join(parts)

    def t_task_write(self, args: dict) -> str:
        root, task = self._task(args)
        name = _file_name(_str(args, "file", required=True))
        content = _str(args, "content", required=True)
        if not any(fnmatch.fnmatch(name, pat) for pat in self.profile["write"]):
            raise ToolError(f"profile '{self.profile_name}' may not write {name}.md "
                            f"(allowed: {', '.join(self.profile['write'])})")
        if len(content) > MAX_WRITE_CHARS:
            raise ToolError(f"content too large ({len(content)} > {MAX_WRITE_CHARS} chars)")
        path = task / f"{name}.md"
        if args.get("append") and path.is_file():
            content = path.read_text(encoding="utf-8").rstrip() + "\n\n" + content
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return f"wrote {path.relative_to(root).as_posix()} (~{human(estimate(content))} tok)"

    def t_task_create(self, args: dict) -> str:
        root = self._project()
        title = _str(args, "title", required=True)
        route = router.summary(title, cfg.load(root))
        task = tasks.create(root, title, route)
        brief = _str(args, "brief")
        if brief:
            (task / "brief.md").write_text(
                f"# {title}\n\n- id: `{task.name}`\n- 추천 라우팅: {route}\n\n{brief.strip()}\n", encoding="utf-8")
        return f"created {task.name}\nroute: {route}"

    def t_pack(self, args: dict) -> str:
        root, task = self._task(args)
        refs_text = ""
        refs = _str(args, "refs")
        if refs:
            stem = refs.removesuffix(".md")
            path = task / f"{stem}.md" if FILE_RE.match(stem) else root / refs
            if not packmod.inside(root, path) or not path.is_file():
                raise ToolError(f"refs file not found: {refs}")
            refs_text = path.read_text(encoding="utf-8")
        budget = args.get("budget")
        if budget is not None and (not isinstance(budget, int) or budget <= 0):
            raise ToolError("'budget' must be a positive integer")
        built = packmod.build(root, refs_text=refs_text, full=_strs(args, "full"),
                              outlines=_strs(args, "outline"), diff_ref=_str(args, "diff") or None,
                              budget=budget or cfg.load(root)["budget"]["pack_tokens"])
        text = built.render(packmod.header(task.name, built))
        (task / "pack.md").write_text(text, encoding="utf-8")
        lines = [f"pack.md: {len(built.sections)} sections, ~{human(built.tokens)} tok"]
        lines += [f"- {s.title}" for s in built.sections]
        lines += [f"- skipped: {s}" for s in built.skipped]
        if args.get("return_content"):
            lines += ["", text]
        return "\n".join(lines)

    def t_route(self, args: dict) -> str:
        routes = router.route(_str(args, "text", required=True), cfg.load(self.root))
        return "\n".join(f"{r.agent} (score {r.score}): {r.reason}" for r in routes)

    def t_handoff(self, args: dict) -> str:
        root, task = self._task(args)
        prompt = tasks.handoff(root, task, _str(args, "to", required=True), _str(args, "ask"))
        size = estimate(prompt.read_text(encoding="utf-8"))
        return f"wrote {prompt.relative_to(root).as_posix()} (~{human(size)} tok) — paste it into the target tool"

    def t_usage(self, args: dict) -> str:
        days = args.get("days", 7)
        if not isinstance(days, (int, float)) or days <= 0:
            raise ToolError("'days' must be a positive number")
        since = usage.since_iso(days)
        report = {"days": days,
                  "claude": usage.total_of(usage.scan_claude(since)).as_dict(),
                  "codex": usage.total_of(usage.scan_codex(since)).as_dict()}
        return json.dumps(report, ensure_ascii=False)


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------- client config snippets

def client_config(client: str, profile: str, root: Path | None) -> str:
    args = ["mcp", "--profile", profile] + (["--root", str(root)] if root else [])
    if client == "claude":
        return json.dumps({"mcpServers": {"relay": {"command": "relay", "args": args}}}, indent=2)
    if client == "codex":
        return "\n".join(["# ~/.codex/config.toml", "[mcp_servers.relay]", 'command = "relay"',
                          "args = [" + ", ".join(json.dumps(a) for a in args) + "]"])
    if client == "antigravity":
        return json.dumps({"mcpServers": {"relay": {"command": "relay", "args": args}}}, indent=2)
    raise ValueError(f"unknown client: {client}")


DEFAULT_PROFILE = {"claude": "claude", "codex": "builder", "antigravity": "explorer"}
