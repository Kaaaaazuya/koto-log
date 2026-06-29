"""CLI 結線のテスト（結合：LLM 非依存部分）。

build_agent が config から DB・Agent を正しく結線することを確認する。
`cfg` フィクスチャは conftest が提供する。対話の一気通しは e2e で行う。
"""

from kotolog.cli import build_agent


def test_build_agent_wires_conn_to_db(cfg):
    from kotolog.db import crud
    from kotolog.tools.executor import ToolExecutor

    agent = build_agent(cfg)
    # conn を直接叩いて DB が変わる（= 結線できている）
    child_id = crud.get_default_child_id(agent.conn)
    executor = ToolExecutor(conn=agent.conn, child_id=child_id, now=None)
    result = executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "3時"})
    assert result["ok"] is True
    rows = agent.conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()
    assert rows["n"] == 1


def test_build_agent_resolves_default_child_from_db(cfg):
    """既定児を DB から解決し、agent.conn に DB 接続が正しく格納されている。"""
    from kotolog.db import crud

    agent = build_agent(cfg)
    conn = agent.conn
    assert crud.get_default_child_id(conn) is not None
    # 子は1人だけ作成されている（重複しない）
    assert len(crud.list_children(conn)) == 1
