from __future__ import annotations

import ast
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).with_name("server.py")


class SessionPersistenceTest(unittest.TestCase):
    def test_runner_uses_session_for_history(self) -> None:
        tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))

        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        run_streamed_calls = [
            node
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == "run_streamed"
        ]

        self.assertTrue(run_streamed_calls)
        self.assertTrue(
            any(keyword.arg == "session" for call in run_streamed_calls for keyword in call.keywords)
        )


if __name__ == "__main__":
    unittest.main()
