from __future__ import annotations

import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
MAIN_PATH = BACKEND_DIR / "main.py"
GUARDRAILS_PATH = BACKEND_DIR / "ecommerce" / "guardrails.py"


class AgentProxyConfigTest(unittest.TestCase):
    def test_main_loads_provider_proxy_from_key_file(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertIn("API key.txt", source)
        self.assertIn("AI巴士", source)
        self.assertIn("set_default_openai_client", source)

    def test_ecommerce_guardrails_use_gpt55_default_client(self) -> None:
        source = GUARDRAILS_PATH.read_text(encoding="utf-8")

        self.assertIn('GUARDRAIL_MODEL = "gpt-5.5"', source)
        self.assertNotIn("qwen-plus", source)
        self.assertNotIn("DASHSCOPE_API_KEY", source)


if __name__ == "__main__":
    unittest.main()
