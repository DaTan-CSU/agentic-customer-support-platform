from __future__ import annotations

import unittest
from pathlib import Path


CHATKIT_PANEL_PATH = Path(__file__).parents[1] / "ui" / "components" / "chatkit-panel.tsx"


class ChatKitEffectsTest(unittest.TestCase):
    def test_on_effect_does_not_use_arrow_arguments(self) -> None:
        source = CHATKIT_PANEL_PATH.read_text(encoding="utf-8")

        self.assertNotIn("arguments as any", source)


if __name__ == "__main__":
    unittest.main()
