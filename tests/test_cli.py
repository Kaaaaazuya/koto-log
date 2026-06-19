"""T1.6: CLI 結線のテスト（LLM 非依存部分）。

build_agent が config から DB・executor を正しく結線することを確認する。
対話そのものは手動／ライブ確認で行う。
"""

from kotolog.cli import build_agent
from kotolog.config import Config


def _cfg() -> Config:
    return Config(
        model="ollama_chat/qwen2.5:7b",
        api_key=None,
        db_url=":memory:",
        default_child="baby",
        ollama_base="http://localhost:11434",
    )


def test_build_agent_wires_executor_to_db():
    agent = build_agent(_cfg())
    # executor を直接叩いて DB が変わる（= 結線できている）
    result = agent.executor.execute(
        "save_record", {"type": "feeding", "amount": 100, "started_at": "3時"}
    )
    assert result["ok"] is True
    rows = agent.executor.conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()
    assert rows["n"] == 1
