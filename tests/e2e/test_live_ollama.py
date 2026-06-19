"""実 Ollama を使う E2E スモークテスト（live）。

Ollama 未起動なら自動スキップ。モデル出力は非決定論的なので、ツール選択の
正しさはここでは断定しない（それは evals/tool_selection.py が定量化する）。
ここでは「スタック全体が実モデル込みで例外なく往復し、文字列を返す」配線だけ
を確認する。

実行例:
    uv run pytest -m live          # ライブだけ
    uv run pytest -m "not live"    # ライブを除外（既定の高速スイート）
"""

import urllib.request

import pytest

from kotolog.cli import build_agent


def _ollama_up(base: str = "http://localhost:11434") -> bool:
    try:
        urllib.request.urlopen(base + "/api/tags", timeout=1)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _ollama_up(), reason="Ollama 未起動のためスキップ"),
]


def test_live_roundtrip_returns_text(cfg):
    agent = build_agent(cfg)
    reply = agent.handle("3時にミルク120ml飲んだ")
    assert isinstance(reply, str) and reply.strip()
