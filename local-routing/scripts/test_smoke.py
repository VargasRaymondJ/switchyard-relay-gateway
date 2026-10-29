"""Focused contract tests; no provider calls or mock provider service."""

import http.server
import threading
import time
import unittest

from smoke import assert_response, find_decisions, request


class ResponseTests(unittest.TestCase):
    def test_agent_json(self):
        assert_response(b'{"content":"hello"}', stream=False, agent=True)

    def test_empty_json_fails(self):
        with self.assertRaises(AssertionError):
            assert_response(b'{"content":""}', stream=False, agent=True)

    def test_agent_stream(self):
        assert_response(
            b'event: token\ndata: {"content":"hello"}\n\nevent: done\ndata: {}\n\n',
            stream=True,
            agent=True,
        )

    def test_premature_stream_fails(self):
        with self.assertRaises(AssertionError):
            assert_response(b'event: token\ndata: {"content":"hello"}\n\n', stream=True, agent=True)

    def test_sse_error_fails(self):
        with self.assertRaises(AssertionError):
            assert_response(b'event: error\ndata: {"message":"failed"}\n\n', stream=True, agent=True)

    def test_openai_stream(self):
        assert_response(
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n',
            stream=True,
            agent=False,
        )

    def test_decision_envelope(self):
        decision = {"selected_model": "target", "algorithm": "passthrough"}
        self.assertEqual(find_decisions({"data": decision}), [decision])


class RequestTests(unittest.TestCase):
    def setUp(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(0.2)
                try:
                    self.send_response(401 if self.path == "/error" else 200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except BrokenPipeError:
                    pass

            def log_message(self, *_args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_http_status_and_closed_connection(self):
        self.assertEqual(request(self.url + "/"), (200, b"{}"))
        self.assertEqual(request(self.url + "/error"), (401, b"{}"))

    def test_deadline_includes_response_headers(self):
        with self.assertRaises(TimeoutError):
            request(self.url + "/slow", timeout=0.05)


if __name__ == "__main__":
    unittest.main()
