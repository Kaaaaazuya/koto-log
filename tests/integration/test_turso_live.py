"""T3.2 ライブ: Turso 実DBへの疎通テスト。

KOTOLOG_DB_URL=libsql://... かつ TURSO_AUTH_TOKEN が設定されているときのみ実行。
通常の `uv run pytest` では自動スキップされる（`-m live` で明示指定する）。
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from kotolog.db import crud
from kotolog.db.connection import connect

_NOW = "2026-06-19T10:00:00+09:00"


@pytest.mark.live
def test_turso_crud_roundtrip():
    load_dotenv()
    url = os.getenv("KOTOLOG_DB_URL", "")
    token = os.getenv("TURSO_AUTH_TOKEN", "")
    if not url.startswith("libsql://") or not token:
        pytest.skip("KOTOLOG_DB_URL が libsql:// でないか TURSO_AUTH_TOKEN 未設定")

    conn = connect(url, auth_token=token)
    crud.init_db(conn)
    child_id = crud.ensure_child(conn, "_test_child")
    record_id = crud.insert_record(conn, child_id=child_id, type="feeding", started_at=_NOW)

    row = crud.get_record(conn, record_id)
    assert row["type"] == "feeding"

    crud.delete_record(conn, record_id)  # クリーンアップ
