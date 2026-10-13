import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from relay import events, mcp_server, tasks

REPO = Path(__file__).resolve().parents[1]


class McpTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        tasks.scaffold(self.root)
        (self.root / "app.py").write_text("def handler(event):\n    return event\n")
        self.out = io.StringIO()

    def tearDown(self):
        self._tmp.cleanup()

    def server(self, profile="full", root="default"):
        return mcp_server.Server(profile, self.root if root == "default" else root, out=self.out)

    def call(self, server, name, **arguments):
        resp = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
        return resp["result"]["content"][0]["text"], resp["result"]["isError"]

    # ------------------------------------------------------------ protocol

    def test_initialize_negotiates_version(self):
        s = self.server()
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code"}, "capabilities": {}}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(s.client, "claude-code")
        r = s.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSIONS[0])

    def test_notifications_get_no_response_and_unknown_method_errors(self):
        s = self.server()
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(s.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"], -32601)
        self.assertEqual(s.handle({"jsonrpc": "2.0", "id": 4, "method": "ping"})["result"], {})

    def test_profiles_limit_tools(self):
        s = self.server("explorer")
        names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["relay_task_list", "relay_task_read", "relay_task_write"])
        r = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "relay_pack", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_definitions_stay_small(self):
        # 도구 정의는 Claude 의 매 요청에 들어가므로 예산을 테스트로 고정한다.
        self.assertLess(mcp_server.definition_tokens("claude"), 700)
        self.assertLess(mcp_server.definition_tokens("explorer"), 350)

    # ------------------------------------------------------------ tools

    def test_create_write_read_flow(self):
        s = self.server()
        text, err = self.call(s, "relay_task_create", title="handler 구조 파악", brief="## Goal\nmap it")
        self.assertFalse(err)
        self.assertIn("001-handler-구조-파악", text)
        self.assertIn("antigravity", text)
        text, err = self.call(s, "relay_task_write", file="explore", content="- app.py:1-2 — entry")
        self.assertFalse(err, text)
        text, err = self.call(s, "relay_pack", refs="explore", return_content=True)
        self.assertFalse(err, text)
        self.assertIn("def handler", text)
        text, _ = self.call(s, "relay_task_read", task="1", files=["brief", "pack", "state"])
        self.assertIn("map it", text)
        self.assertIn("def handler", text)
        self.assertIn("## state.md\n(none)", text)
        self.call(s, "relay_task_write", file="state", content="one")
        self.call(s, "relay_task_write", file="state", content="two", append=True)
        self.assertEqual((tasks.resolve(self.root, "1") / "state.md").read_text(), "one\n\ntwo\n")
        self.assertIn("001-handler", self.call(s, "relay_task_list")[0])

    def test_write_permissions_and_validation(self):
        tasks.create(self.root, "t", "x")
        s = self.server("explorer")
        text, err = self.call(s, "relay_task_write", file="brief", content="hijack")
        self.assertTrue(err)
        self.assertIn("may not write", text)
        for bad in ("../../etc/passwd", "state/../../x", "notes"):
            text, err = self.call(s, "relay_task_write", file=bad, content="x")
            self.assertTrue(err, bad)
        text, err = self.call(s, "relay_task_read", task="../..", files=["brief"])
        self.assertTrue(err)

    def test_pack_refuses_paths_outside_root(self):
        tasks.create(self.root, "t", "x")
        outside = self.root.parent / f"{self.root.name}-secret.txt"
        outside.write_text("TOPSECRET\nline2\n")
        self.addCleanup(outside.unlink)
        s = self.server()
        self.call(s, "relay_task_write", file="explore", content=f"- ../{outside.name}:1-2")
        text, err = self.call(s, "relay_pack", refs="explore", full=["../*", str(outside)],
                              return_content=True)
        self.assertFalse(err)
        self.assertNotIn("TOPSECRET", text)
        self.assertIn("프로젝트 밖", text)
        text, err = self.call(s, "relay_pack", refs="../../etc/hosts")
        self.assertTrue(err)

    def test_requires_relay_dir(self):
        with tempfile.TemporaryDirectory() as empty:
            s = mcp_server.Server("full", Path(empty), out=self.out)
            text, err = self.call(s, "relay_task_list")
            self.assertTrue(err)
            self.assertIn("relay init", text)
            text, err = self.call(s, "relay_route", text="debug race condition")  # 루트 불필요
            self.assertFalse(err)
            self.assertTrue(text.startswith("claude"))

    def test_events_are_recorded(self):
        s = self.server("builder")
        s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex"}}})
        self.call(s, "relay_task_list")
        self.call(s, "relay_task_write", file="brief", content="x")  # 권한 오류도 기록
        summary = events.summarize(events.read(self.root))
        self.assertEqual(summary[("codex", "relay_task_list")]["calls"], 1)
        self.assertEqual(summary[("codex", "relay_task_write")]["errors"], 1)

    def test_roots_from_client(self):
        s = mcp_server.Server("full", None, out=self.out)
        s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"capabilities": {"roots": {"listChanged": True}}}})
        s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        request = json.loads(self.out.getvalue().splitlines()[-1])
        self.assertEqual(request["method"], "roots/list")
        s.handle({"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [
            {"uri": "file:///nonexistent-relay-root"}, {"uri": (self.root / "app.py").as_uri()}]}})
        self.assertEqual(s.root, self.root)

    def test_client_config(self):
        snippet = mcp_server.client_config("codex", "builder", Path("/p"))
        self.assertIn('[mcp_servers.relay]', snippet)
        self.assertIn('"--root", "/p"', snippet)
        data = json.loads(mcp_server.client_config("claude", "claude", None))
        self.assertEqual(data["mcpServers"]["relay"]["args"], ["mcp", "--profile", "claude"])


class StdioEndToEndTest(unittest.TestCase):
    """실제 프로세스를 띄워 stdio 로 JSON-RPC 를 주고받는다 (클라이언트가 보는 그대로)."""

    def test_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks.scaffold(Path(tmp))
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "relay_task_create", "arguments": {"title": "e2e"}}},
                "not json",
            ]
            stdin = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n"
            env = {**os.environ, "PYTHONPATH": str(REPO)}
            proc = subprocess.run([sys.executable, "-m", "relay", "mcp", "--profile", "claude", "--root", tmp],
                                  input=stdin, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            replies = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual([r.get("id") for r in replies], [1, 2, 3, None])
            self.assertEqual(len(replies[1]["result"]["tools"]), 6)
            self.assertIn("created 001-e2e", replies[2]["result"]["content"][0]["text"])
            self.assertEqual(replies[3]["error"]["code"], -32700)
            self.assertTrue((Path(tmp) / ".relay" / "events.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
