"""DB 接続（T1.1）。

試作はローカル SQLite。行は dict 風にアクセスできる sqlite3.Row を使う。
本番（Turso/libSQL）への差し替えはこのモジュールを起点に行う。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any


class _LibsqlCursor:
    """libsql_experimental の Cursor ラッパー。fetchone/fetchall を dict に変換する。"""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def lastrowid(self) -> int | None:
        return self._raw.lastrowid

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    def _as_dict(self, row: Any) -> dict | None:
        if row is None:
            return None
        return {col[0]: row[i] for i, col in enumerate(self._raw.description)}

    def fetchone(self) -> dict | None:
        return self._as_dict(self._raw.fetchone())

    def fetchall(self) -> list[dict]:
        return [self._as_dict(r) for r in self._raw.fetchall()]  # type: ignore[misc]


class _LibsqlConn:
    """libsql_experimental.Connection のラッパー。

    row_factory が使えないため execute() の結果に dict 変換を適用する。
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        # 複数スレッドから同一接続を共有するため、read-then-write な複合操作を
        # 呼び出し側で直列化するためのロック（Issue #33）。
        self.lock = threading.RLock()

    def execute(self, sql: str, parameters: tuple | list = ()) -> _LibsqlCursor:
        if isinstance(parameters, list):
            parameters = tuple(parameters)
        return _LibsqlCursor(self._raw.execute(sql, parameters))

    def executescript(self, sql: str) -> None:
        self._raw.executescript(sql)

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()


class _KotoSqliteConnection(sqlite3.Connection):
    """`lock` 属性を持たせるための sqlite3.Connection サブクラス（Issue #33）。

    sqlite3.Connection は任意属性の代入を許さないため、素の Connection には
    ロックを持たせられない。isinstance(conn, sqlite3.Connection) は維持される。
    """


def connect(db_url: str, auth_token: str | None = None) -> Any:
    """SQLite または Turso/libSQL 接続を返す。

    db_url が libsql:// で始まる場合は libsql_experimental で Turso に接続する。
    それ以外は sqlite3（ローカルファイルまたは :memory:）を使う。
    """
    if db_url.startswith("libsql://"):
        import libsql_experimental as libsql  # type: ignore[import]

        conn = _LibsqlConn(libsql.connect(database=db_url, auth_token=auth_token or ""))
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    # check_same_thread=False: webhook の BackgroundTask は別スレッドで動く(P2)
    conn = sqlite3.connect(db_url, check_same_thread=False, factory=_KotoSqliteConnection)
    conn.row_factory = sqlite3.Row
    # 複数スレッドから同一接続を共有するため、read-then-write な複合操作を
    # 呼び出し側で直列化するためのロック（Issue #33）。
    conn.lock = threading.RLock()
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: 同時読み取りを妨げず、書き込み時の "database is locked" を抑える。
    # busy_timeout: 書き込みロック競合時に即エラーにせず一定時間待つ。
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
