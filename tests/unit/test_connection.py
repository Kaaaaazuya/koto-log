"""T3.2: connection.connect() のブランチ分岐テスト。

libsql:// URL のとき libsql_experimental を呼ぶことと、
それ以外のとき sqlite3 を使うことを検証する。
"""

from __future__ import annotations

import sqlite3
import sys
from unittest.mock import MagicMock

from kotolog.db.connection import _LibsqlConn, connect


def test_sqlite_url_returns_sqlite3_connection():
    conn = connect(":memory:")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_libsql_url_delegates_to_libsql_experimental(monkeypatch):
    mock_conn = MagicMock()
    mock_libsql = MagicMock()
    mock_libsql.connect.return_value = mock_conn
    monkeypatch.setitem(sys.modules, "libsql_experimental", mock_libsql)

    result = connect("libsql://koto-log.turso.io", auth_token="tok123")

    mock_libsql.connect.assert_called_once_with(database="libsql://koto-log.turso.io", auth_token="tok123")
    assert isinstance(result, _LibsqlConn)
    assert result._raw is mock_conn


def test_libsql_url_without_token_passes_empty_string(monkeypatch):
    mock_libsql = MagicMock()
    mock_libsql.connect.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "libsql_experimental", mock_libsql)

    connect("libsql://koto-log.turso.io")

    _, kwargs = mock_libsql.connect.call_args
    assert kwargs["auth_token"] == ""
