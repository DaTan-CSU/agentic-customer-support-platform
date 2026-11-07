from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
MAIN_PATH = BACKEND_DIR / "main.py"
GUARDRAILS_PATH = BACKEND_DIR / "ecommerce" / "guardrails.py"


class AgentProxyConfigTest(unittest.TestCase):
    def _run_isolated(self, code: str, **env_overrides: str) -> str:
        env = os.environ.copy()
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "KEY_FILE_PATH",
            "TENCENTCLOUD_SECRET_ID",
            "TENCENTCLOUD_SECRET_KEY",
            "DATA_DIR",
        ):
            env.pop(key, None)
        env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_main_loads_provider_proxy_from_key_file(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertIn("KEY_FILE_PATH", source)
        self.assertIn("AI巴士", source)
        self.assertIn("set_default_openai_client", source)

    def test_ecommerce_guardrails_use_lightweight_default_client(self) -> None:
        source = GUARDRAILS_PATH.read_text(encoding="utf-8")

        self.assertIn('GUARDRAIL_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")', source)
        self.assertNotIn("qwen-plus", source)
        self.assertNotIn("DASHSCOPE_API_KEY", source)

    def test_model_override_reaches_all_runtime_agents(self) -> None:
        output = self._run_isolated(
            """
from ecommerce.agents import triage_agent
from ecommerce.guardrails import guardrail_agent, jailbreak_guardrail_agent
from ecommerce.output_guardrails import _compliance_judge
from ecommerce.summary_agent import case_summary_agent
agents = (triage_agent, guardrail_agent, jailbreak_guardrail_agent, _compliance_judge, case_summary_agent)
assert all(agent.model == 'config-test-model' for agent in agents), [agent.model for agent in agents]
""",
            OPENAI_MODEL="config-test-model",
        )
        self.assertEqual(output, "")

    def test_data_dir_controls_session_and_trace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            output = self._run_isolated(
                """
from pathlib import Path
import os
import server
import tracing_store
expected = Path(os.environ['DATA_DIR'])
assert server.SESSION_DB_PATH == expected / '.agent_sessions.db'
assert tracing_store.TRACE_DB_PATH == expected / '.agent_traces.db'
""",
                DATA_DIR=data_dir,
            )
        self.assertEqual(output, "")

    def test_explicit_key_file_loads_text_and_tencent_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "keys.txt"
            key_file.write_text(
                "词元神\nhttps://example.test\n代码调用：sk-fixture\n"
                "腾讯云密钥\nSecretId sid-fixture\nSecretKey skey-fixture\n",
                encoding="utf-8",
            )
            output = self._run_isolated(
                """
import main
import os
assert main._base_url == 'https://example.test/v1'
assert main._api_key == 'sk-fixture'
assert os.environ['TENCENTCLOUD_SECRET_ID'] == 'sid-fixture'
assert os.environ['TENCENTCLOUD_SECRET_KEY'] == 'skey-fixture'
""",
                KEY_FILE_PATH=str(key_file),
                DATA_DIR=str(Path(temp_dir) / "data"),
            )
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
