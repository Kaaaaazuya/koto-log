"""共有テストフィクスチャとフェイク。

テストは tests/unit・tests/integration・tests/e2e に分かれる。配置フォルダから
対応するマーカー（unit/integration/e2e）を自動付与するので、`pytest -m unit`
のように層を選んで実行できる。
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from kotolog.config import Config
from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.tools.executor import ToolExecutor

JST = timezone(timedelta(hours=9))
# 全テスト共通の基準時刻。相対表現や period 解決を決定論的にする。
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)

OLLAMA_BASE = "http://localhost:11434"


# --- マーカー自動付与・live のオプトイン -------------------------------------
def pytest_collection_modifyitems(config, items):
    # 配置フォルダ → 層マーカーを自動付与（pytest -m unit 等で選べる）
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        for layer in ("unit", "integration", "e2e"):
            if f"/tests/{layer}/" in path:
                item.add_marker(getattr(pytest.mark, layer))

    # live は遅く環境依存。`-m live` で明示指定したときだけ実行する。
    markexpr = config.getoption("markexpr") or ""
    if "live" in markexpr:
        return
    skip_live = pytest.mark.skip(reason="live は `-m live` 指定時のみ実行")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# --- 時刻・DB ----------------------------------------------------------------
@pytest.fixture()
def now() -> datetime:
    return NOW


@pytest.fixture()
def conn():
    """初期化済みのインメモリ DB 接続。"""
    c = connect(":memory:")
    crud.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def child_id(conn) -> int:
    return crud.ensure_child(conn, "baby")


@pytest.fixture()
def executor(conn, child_id) -> ToolExecutor:
    return ToolExecutor(conn=conn, child_id=child_id, now=NOW)


@pytest.fixture()
def cfg() -> Config:
    return Config(
        model="ollama_chat/qwen2.5:7b",
        api_key=None,
        db_url=":memory:",
        default_child="baby",
        ollama_base=OLLAMA_BASE,
        line_channel_secret=None,
        line_channel_access_token=None,
    )


# --- LLM フェイク ------------------------------------------------------------
class FakeLLM:
    """scripted な応答を順に返し、渡された messages を記録するフェイク LLM。"""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.seen_messages: list = []

    def complete(self, messages, tools=None):
        self.seen_messages.append(messages)
        return self.scripted.pop(0)


def make_resp(content=None, tool_calls=None):
    """LiteLLM の completion 応答を模した構造を作る。"""
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def make_tc(name, args, id="call_1"):
    """ネイティブ tool_call を模した構造を作る。"""
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


@pytest.fixture()
def fake_llm():
    """scripted 応答列から FakeLLM を作るファクトリ。"""
    return lambda scripted: FakeLLM(scripted)


@pytest.fixture()
def resp():
    return make_resp


@pytest.fixture()
def tc():
    return make_tc


# --- ライブ Ollama -----------------------------------------------------------
def ollama_available(base: str = OLLAMA_BASE) -> bool:
    try:
        urllib.request.urlopen(base + "/api/tags", timeout=1)
        return True
    except Exception:  # noqa: BLE001 - 到達不能はすべて「利用不可」とみなす
        return False
