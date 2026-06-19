"""DB 接続（T1.1）。

試作はローカル SQLite。行は dict 風にアクセスできる sqlite3.Row を使う。
本番（Turso/libSQL）への差し替えはこのモジュールを起点に行う。
"""

from __future__ import annotations

import sqlite3


def connect(db_url: str) -> sqlite3.Connection:
    """SQLite 接続を返す。`:memory:` またはファイルパスを受け付ける。"""
    # check_same_thread=False: webhook の BackgroundTask は別スレッドで動く(P2)
    conn = sqlite3.connect(db_url, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
