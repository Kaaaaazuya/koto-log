"""ツール executor のテスト（結合：直接呼び出しで DB が変わる）。

`executor` フィクスチャは conftest が提供（基準時刻 NOW=2026-06-18 10:00 JST）。
"""

import pytest

from kotolog.db import crud


def test_save_record_inserts_row(executor):
    result = executor.execute(
        "save_record",
        {"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml", "started_at": "3時"},
    )
    assert result["ok"] is True
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["amount"] == 120
    # 「3時」が JST 絶対時刻に正規化されている
    assert rec["started_at"] == "2026-06-18T03:00:00+09:00"


def test_query_records_aggregates(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    executor.execute("save_record", {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "7時"})
    executor.execute("save_record", {"type": "diaper", "started_at": "8時"})

    result = executor.execute("query_records", {"type": "feeding", "period": "today"})
    assert result["count"] == 2
    assert result["total_amount"] == 220


def test_update_last_record(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    result = executor.execute(
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"amount": 150}},
    )
    assert result["ok"] is True
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["amount"] == 150


def test_update_normalizes_time_in_new_values(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "3時"})
    result = executor.execute(
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"started_at": "5時"}},
    )
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["started_at"] == "2026-06-18T05:00:00+09:00"


def test_delete_last_record(executor):
    saved = executor.execute("save_record", {"type": "feeding", "started_at": "3時"})
    rid = saved["record"]["id"]
    result = executor.execute("update_or_delete_record", {"target": "last", "action": "delete"})
    assert result["ok"] is True
    assert crud.get_record(executor.conn, rid) is None


def test_update_or_delete_with_no_record(executor):
    result = executor.execute("update_or_delete_record", {"target": "last", "action": "delete"})
    assert result["ok"] is False


def test_unknown_tool_raises(executor):
    with pytest.raises(ValueError):
        executor.execute("nope", {})


# --- T1.8: 集計化・sub_type 正規化 -------------------------------------------
def test_save_normalizes_sub_type(executor):
    res = executor.execute("save_record", {"type": "feeding", "sub_type": "おっぱい", "started_at": "3時"})
    rec = crud.get_record(executor.conn, res["record"]["id"])
    assert rec["sub_type"] == "母乳"


def test_query_returns_by_type_breakdown(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "3時"})
    executor.execute("save_record", {"type": "feeding", "amount": 120, "started_at": "7時"})
    executor.execute("save_record", {"type": "diaper", "started_at": "8時"})

    res = executor.execute("query_records", {"period": "today"})

    assert res["by_type"]["feeding"] == {"count": 2, "total_amount": 220}
    assert res["by_type"]["diaper"]["count"] == 1


def test_query_returns_by_sub_type_breakdown(executor):
    executor.execute("save_record", {"type": "feeding", "sub_type": "母乳", "started_at": "3時"})
    executor.execute(
        "save_record", {"type": "feeding", "sub_type": "ミルク", "amount": 120, "started_at": "5時"}
    )
    executor.execute(
        "save_record",
        {"type": "feeding", "sub_type": "粉ミルク", "amount": 100, "started_at": "7時"},
    )

    res = executor.execute("query_records", {"type": "feeding", "period": "today"})

    # 「粉ミルク」は正規化で「ミルク」に合流して数えられる
    assert res["by_sub_type"]["ミルク"]["count"] == 2
    assert res["by_sub_type"]["母乳"]["count"] == 1


def test_query_sub_type_filter_normalizes(executor):
    executor.execute(
        "save_record", {"type": "feeding", "sub_type": "ミルク", "amount": 120, "started_at": "5時"}
    )
    executor.execute("save_record", {"type": "feeding", "sub_type": "母乳", "started_at": "3時"})

    # フィルタ値「粉ミルク」も正規化してから絞り込む
    res = executor.execute("query_records", {"type": "feeding", "sub_type": "粉ミルク", "period": "today"})

    assert res["count"] == 1
    assert res["sub_type"] == "ミルク"


# --- T1.11: latest（前回いつ／何時間前） -------------------------------------
def test_query_latest_returns_record_and_elapsed(executor):
    # NOW = 2026-06-18 10:00。授乳は 7時(3時間前)、おむつは 9時。
    executor.execute("save_record", {"type": "feeding", "amount": 120, "started_at": "7時"})
    executor.execute("save_record", {"type": "diaper", "started_at": "9時"})

    res = executor.execute("query_records", {"type": "feeding", "period": "latest"})

    assert res["ok"] is True
    assert res["record"]["type"] == "feeding"
    assert res["elapsed_minutes"] == 180
    assert res["elapsed_hours"] == 3.0


def test_query_latest_no_record(executor):
    res = executor.execute("query_records", {"type": "feeding", "period": "latest"})
    assert res["ok"] is False
