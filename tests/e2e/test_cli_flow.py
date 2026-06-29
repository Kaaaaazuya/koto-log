"""CLI 一気通し E2E（決定論版）。

実スタック（ファイルDB + crud + executor + Agent）を組み、LLM だけ FakeLLM に
差し替えて「保存→集計→修正→取消」の会話を通す。返信・DB状態・再接続後の
永続化までを検証する。モデル非依存なので CI で安定して緑になる。
"""

from datetime import datetime, timedelta, timezone

from kotolog.agent.loop import Agent
from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.tools.executor import ToolExecutor

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)


def test_full_conversation_persists_to_file_db(tmp_path, fake_llm, resp, tc):
    db_path = str(tmp_path / "e2e.db")
    conn = connect(db_path)
    crud.init_db(conn)
    crud.ensure_child(conn, "baby")

    llm = fake_llm(
        [
            # 1) 保存: extract_records プレパス（空 → メインループへ）→ save_record → 確認文
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(
                tool_calls=[
                    tc(
                        "save_record",
                        {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "3時"},
                    )
                ]
            ),
            resp(content="ミルク120mlを3時に記録しました。"),
            # 2) 集計: extract_records プレパス（空）→ query_records → 集計結果
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("query_records", {"type": "feeding", "period": "today"})]),
            resp(content="今日は1回、合計120mlです。"),
            # 3) 修正: extract_records プレパス（空）→ update → 確認文
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(
                tool_calls=[
                    tc(
                        "update_or_delete_record",
                        {"target": "last", "action": "update", "new_values": {"amount": 150}},
                    )
                ]
            ),
            resp(content="直近の記録を150mlに修正しました。"),
            # 4) 取消: extract_records プレパス（空）→ delete → 確認文
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("update_or_delete_record", {"target": "last", "action": "delete"})]),
            resp(content="直近の記録を取り消しました。"),
        ]
    )
    agent = Agent(client=llm, conn=conn, _now=lambda: NOW)

    # 1) 保存 → DBに1件
    r1 = agent.handle("3時にミルク120ml飲んだ")
    assert "記録しました" in r1
    assert conn.execute("SELECT amount FROM records").fetchone()["amount"] == 120

    # 2) 集計 → tool結果(count=1)が2手目のLLM入力に渡る
    r2 = agent.handle("今日は何回飲んだ？")
    assert "120" in r2
    query_step_msgs = llm.seen_messages[5]
    tool_msgs = [m for m in query_step_msgs if m.get("role") == "tool"]
    assert tool_msgs and '"count": 1' in tool_msgs[0]["content"]

    # 3) 修正 → 直近が150に
    r3 = agent.handle("やっぱり150に直して")
    assert "150" in r3
    assert conn.execute("SELECT amount FROM records").fetchone()["amount"] == 150

    # 別接続で永続化を確認（ファイルDBに150が反映されている）
    mid = connect(db_path)
    assert mid.execute("SELECT amount FROM records").fetchone()["amount"] == 150
    mid.close()

    # 4) 取消 → 0件
    r4 = agent.handle("さっきのなし")
    assert "取り消" in r4
    assert conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"] == 0
    conn.close()


def test_daily_summary_narrates_from_aggregates(tmp_path, fake_llm, resp, tc):
    """T1.10: 「今日のまとめ」が1回の query 集計(by_type)から文章化される。"""
    conn = connect(str(tmp_path / "sum.db"))
    crud.init_db(conn)
    child_id = crud.ensure_child(conn, "baby")
    executor = ToolExecutor(conn=conn, child_id=child_id, now=NOW)

    executor.execute("save_record", {"type": "feeding", "amount": 120, "started_at": "3時"})
    executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "7時"})
    executor.execute("save_record", {"type": "diaper", "started_at": "8時"})

    llm = fake_llm(
        [
            # extract_records プレパス（空 → メインループへ）
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("query_records", {"period": "today"})]),
            resp(content="今日は授乳2回(220ml)、おむつ1回でした。"),
        ]
    )
    agent = Agent(client=llm, conn=conn, _now=lambda: NOW)

    reply = agent.handle("今日のまとめは？")

    assert "授乳" in reply
    # 集計を1回の query で取得し、by_type が3手目のLLM入力に渡っている（[0]=extract, [1]=loop1, [2]=loop2）
    tool_msgs = [m for m in llm.seen_messages[2] if m.get("role") == "tool"]
    assert tool_msgs and '"by_type"' in tool_msgs[0]["content"]
    assert '"feeding"' in tool_msgs[0]["content"]
    conn.close()
