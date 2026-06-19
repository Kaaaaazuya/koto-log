"""CLI 結線のテスト（結合：LLM 非依存部分）。

build_agent が config から DB・executor を正しく結線することを確認する。
`cfg` フィクスチャは conftest が提供する。対話の一気通しは e2e で行う。
"""

from kotolog.cli import build_agent


def test_build_agent_wires_executor_to_db(cfg):
    agent = build_agent(cfg)
    # executor を直接叩いて DB が変わる（= 結線できている）
    result = agent.executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "3時"})
    assert result["ok"] is True
    rows = agent.executor.conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()
    assert rows["n"] == 1
