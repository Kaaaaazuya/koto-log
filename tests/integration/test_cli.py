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


def test_build_agent_resolves_default_child_from_db(cfg):
    """既定児を DB から解決し、executor.child_id が既定児に一致する（KOTOLOG_DEFAULT_CHILD 撤廃）。"""
    from kotolog.db import crud

    agent = build_agent(cfg)
    conn = agent.executor.conn
    assert agent.executor.child_id == crud.get_default_child_id(conn)
    # 子は1人だけ作成されている（重複しない）
    assert len(crud.list_children(conn)) == 1
