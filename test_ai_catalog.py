"""Compatible model discovery uses local HTTP fixtures and synthetic keys only."""
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication
from app.core.ai_catalog import APIModelCatalog

APP = QCoreApplication.instance() or QCoreApplication([])


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/slow/"):
                    time.sleep(.2)
                status = 401 if self.path.startswith("/denied/") else 302 if self.path.startswith("/redirect/") else 200
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/v1/models")
                self.end_headers()
                try:
                    if self.path.startswith("/nested/"):
                        self.wfile.write(b'{"data":' + b'[' * 1100 + b']' * 1100 + b'}')
                        return
                    if self.path.startswith("/malformed/"):
                        self.wfile.write(b'{"data":null}')
                        return
                    if self.path.startswith("/large/"):
                        self.wfile.write(b' ' * (APIModelCatalog.MAX_BYTES + 100))
                        return
                    self.wfile.write(json.dumps({"data": [
                        {"id": "b", "owned_by": "openai"}, {"id": "a", "owned_by": "anthropic"},
                        {"id": "b", "owned_by": "openai"}, {"name": "no id"}]}).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.catalog = APIModelCatalog()
        self.models = []
        self.errors = []
        self.catalog.loaded.connect(self.models.append)
        self.catalog.failed.connect(self.errors.append)

    def tearDown(self):
        self.catalog.cancel()
        self.server.shutdown()
        self.server.server_close()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            APP.processEvents()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Catalog request did not complete")

    def test_url_key_and_exact_deduped_ids(self):
        self.catalog.fetch(self.base + "/v1/", "synthetic-key")
        self.wait_for(lambda: self.models or self.errors)
        self.assertFalse(self.errors)
        self.assertEqual(self.requests, [("/v1/models", "Bearer synthetic-key")])
        self.assertEqual([m["id"] for m in self.models[0]], ["a", "b"])
        self.assertEqual(self.models[0][0]["owner"], "anthropic")

    def test_auth_failure_and_redirect_do_not_expose_key_or_follow_location(self):
        for path in ("denied", "redirect"):
            self.catalog.fetch(self.base + "/" + path, "synthetic-key")
            self.wait_for(lambda: not self.catalog.busy)
        self.assertEqual(len(self.errors), 2)
        self.assertNotIn("synthetic-key", str(self.errors))
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.models)

    def test_cancelled_request_cannot_replace_new_models(self):
        self.catalog.fetch(self.base + "/slow", "synthetic-key")
        self.catalog.cancel()
        self.catalog.fetch(self.base + "/v1", "synthetic-key")
        self.wait_for(lambda: self.models)
        self.assertEqual(len(self.models), 1)
        self.assertFalse(self.errors)

    def test_timeout_leaves_manual_entry_possible(self):
        self.catalog.fetch(self.base + "/slow", "synthetic-key", timeout_ms=30)
        self.wait_for(lambda: self.errors)
        self.assertFalse(self.catalog.busy)
        self.assertFalse(self.models)

    def test_invalid_url_is_rejected_without_a_request(self):
        for url in ("file:///tmp/model", "missing-host", "https://user:secret@example.org/v1"):
            with self.assertRaises(ValueError):
                self.catalog.fetch(url, "synthetic-key")
        self.assertFalse(self.requests)

    def test_bad_format_and_oversized_response_fail_cleanly(self):
        for path in ("malformed", "large"):
            self.catalog.fetch(self.base + "/" + path, "synthetic-key")
            self.wait_for(lambda: not self.catalog.busy)
        self.assertEqual(len(self.errors), 2)
        self.assertFalse(self.models)

    def test_deeply_nested_json_reports_error_without_escaping_qt_callback(self):
        uncaught = []
        with patch.object(sys, "excepthook", lambda *args: uncaught.append(args[0])):
            self.catalog.fetch(self.base + "/nested", "synthetic-key")
            self.wait_for(lambda: self.errors or uncaught)
        self.assertFalse(uncaught)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.catalog.busy)


if __name__ == "__main__":
    unittest.main()
