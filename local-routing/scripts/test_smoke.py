"""Focused contract tests; no provider calls or mock provider service."""

import unittest

from smoke import assert_response, find_decisions


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


if __name__ == "__main__":
    unittest.main()
