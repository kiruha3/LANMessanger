"""Actual installed Pi against a deterministic local OpenAI API; performs a file tool."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication
from app.core.ai import PiProcess, prepare_environment, process_environment, rpc_command

APP = QCoreApplication.instance() or QCoreApplication([])


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Pi integration timed out")


class LocalAPI(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        request_number = len(self.server.requests)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        planned_calls = self.server.tool_plan(request, len(self.server.requests)) if hasattr(self.server, "tool_plan") else None
        if planned_calls is not None:
            delta = {"role": "assistant", "tool_calls": planned_calls}
            reason = "tool_calls"
        elif not hasattr(self.server, "tool_plan") and (not any(m.get("role") == "tool" for m in request["messages"]) or len(self.server.requests) in getattr(self.server, "force_tool_requests", ())):
            delta = {"role": "assistant", "tool_calls": getattr(self.server, "tool_calls", [{"index": 0, "id": "call_write", "type": "function",
                     "function": {"name": getattr(self.server, 'tool_name', 'write'), "arguments": json.dumps(getattr(self.server, 'tool_arguments', {"path": "agent-proof.txt", "content": "created by real Pi"}))}}])}
            reason = "tool_calls"
        else:
            reply = self.server.reply_text(request) if hasattr(self.server, "reply_text") else "Файл создан локальным агентом."
            delta = {"role": "assistant", "content": reply}
            reason = "stop"
        try:
            for index, choice in enumerate(({"index": 0, "delta": delta, "finish_reason": None},
                                            {"index": 0, "delta": {}, "finish_reason": reason})):
                value = {"id": "local-test", "object": "chat.completion.chunk", "model": "test-model", "choices": [choice]}
                self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
                if index == 0 and hasattr(self.server, "after_delta"):
                    self.wfile.flush()
                    self.server.after_delta(request_number)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            if not hasattr(self.server, "after_delta"):
                raise


class RealPiTests(unittest.TestCase):
    def test_rpc_preserves_environment_proxy_support(self):
        try:
            program, cli, _module = rpc_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            script = '''
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
try { await import(pathToFileURL(process.argv[1]).href); }
catch(error) { if (!error.message.includes("Модель не найдена")) throw error; }
const { getGlobalDispatcher } = createRequire(process.argv[2])("undici");
console.log(getGlobalDispatcher().constructor.name);
'''
            environment = process_environment({"PI_CODING_AGENT_DIR": root, "HTTPS_PROXY": "http://127.0.0.1:1"})
            env = dict(entry.split("=", 1) for entry in environment.toStringList())
            result = subprocess.run([program, "--input-type=module", "-e", script, *cli,
                                     "--session", str(Path(root, "proxy-session.jsonl")),
                                     "--provider", "unknown", "--model", "unknown"], cwd=root,
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "EnvHttpProxyAgent")

    def test_real_pi_bash_runs_with_prepared_environment(self):
        try:
            program, cli, _module = rpc_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            project_settings = Path(root, '.pi'); project_settings.mkdir()
            (project_settings / 'settings.json').write_text(json.dumps({'packages': ['npm:lanmessenger-review-nonexistent-sentinel']}))
            guard = Path(root, 'package-guard.mjs')
            guard.write_text('const {DefaultPackageManager}=await import(' + json.dumps((_module.parent / 'core/package-manager.js').as_uri()) + ');DefaultPackageManager.prototype.installParsedSource=async()=>{throw new Error("Project package installation attempted");};')
            server = ThreadingHTTPServer(('127.0.0.1', 0), LocalAPI)
            server.requests = []
            server.tool_name = 'bash'
            server.tool_arguments = {'command':'printf bash-ok > bash-proof.txt; git --version; rg --version; fd --version'}
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            rpc = PiProcess(); events, errors = [], []
            rpc.received.connect(events.append); rpc.error.connect(errors.append)
            config = {'provider':'lanmessenger-api', 'model':'test-model', 'endpoint':f'http://127.0.0.1:{server.server_port}/v1'}
            env = prepare_environment(Path(root), config, 'local-test-key')
            try:
                rpc.start(program, ['--import', guard.as_uri()] + cli + ['--mode','rpc','--provider','lanmessenger-api','--model','test-model','--session',str(Path(root,'bash-session.jsonl')),'--no-extensions','--no-skills'], root, env)
                wait_for(lambda: rpc.ready or errors)
                self.assertEqual(errors, [])
                rpc.send_prompt('Check the shell')
                wait_for(lambda: any(e.get('type') == 'agent_end' for e in events) or errors)
                self.assertEqual(errors, [])
                tool = next(e for e in events if e.get('type') == 'tool_execution_end')
                self.assertFalse(tool.get('isError'), str(tool))
                self.assertEqual(Path(root,'bash-proof.txt').read_text(), 'bash-ok')
            finally:
                rpc.shutdown(); server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_real_pi_writes_file_and_restores_session_without_replay(self):
        try:
            program, cli, _module = rpc_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            rpc = PiProcess()
            events, errors = [], []
            rpc.received.connect(events.append)
            rpc.error.connect(errors.append)
            config = {"provider": "lanmessenger-api", "model": "test-model", "endpoint": f"http://127.0.0.1:{server.server_port}/v1", "thinking": "xhigh"}
            env = prepare_environment(Path(root), config, "local-test-key")
            session = str(Path(root, "session.jsonl"))
            args = cli + ["--mode", "rpc", "--provider", "lanmessenger-api", "--model", "test-model", "--thinking", "xhigh", "--session", session, "--no-extensions", "--no-skills", "--append-system-prompt", "User instruction integration marker"]
            try:
                rpc.start(program, args, root, env)
                wait_for(lambda: rpc.ready or errors)
                self.assertEqual(errors, [])
                rpc.send_prompt("Создай agent-proof.txt")
                wait_for(lambda: any(e.get("type") == "agent_end" for e in events) or errors)
                self.assertEqual(errors, [])
                self.assertEqual(Path(root, "agent-proof.txt").read_text(), "created by real Pi")
                self.assertTrue(any(e.get("type") == "tool_execution_end" for e in events))
                self.assertEqual(len(server.requests), 2)
                self.assertTrue(any(m.get('role') in ('system','developer') and 'User instruction integration marker' in str(m.get('content')) for m in server.requests[0]['messages']))
                self.assertEqual([r.get("reasoning_effort") for r in server.requests], ["xhigh", "xhigh"])
                for level, expected in (("high", "high"), ("off", "none")):
                    events.clear()
                    rpc.command({"type": "set_thinking_level", "level": level})
                    wait_for(lambda: any(e.get("command") == "set_thinking_level" for e in events) or errors)
                    self.assertEqual(errors, [])
                    rpc.send_prompt("Проверка уровня размышлений")
                    wait_for(lambda: any(e.get("type") == "agent_end" for e in events) or errors)
                    self.assertEqual(errors, [])
                    self.assertEqual(server.requests[-1].get("reasoning_effort"), expected)
                rpc.shutdown()
                events.clear()
                rpc.start(program, args, root, env)
                wait_for(lambda: rpc.ready or errors)
                self.assertEqual(errors, [])
                rpc.command({"type": "get_messages"})
                wait_for(lambda: any(e.get("command") == "get_messages" for e in events))
                messages = next(e["data"]["messages"] for e in events if e.get("command") == "get_messages")
                self.assertEqual(sum(m.get("role") == "user" for m in messages), 3)
                self.assertEqual(len(server.requests), 4)
                self.assertTrue(Path(session).exists())
            finally:
                rpc.shutdown()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
