"""設定層（T0.2）。

`.env` から model 名・APIキー・DB URL などを読み込む。
NFR-3: LLM の切替は KOTOLOG_MODEL の文字列変更のみで完結させる。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODEL = "ollama_chat/qwen2.5:7b"
DEFAULT_DB_URL = "kotolog.db"
DEFAULT_CHILD = "baby"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"


@dataclass(frozen=True)
class Config:
    model: str
    api_key: str | None
    db_url: str
    default_child: str
    ollama_base: str
    line_channel_secret: str | None
    line_channel_access_token: str | None
    turso_auth_token: str | None
    dashboard_token: str | None


def load_config() -> Config:
    """環境変数（.env 含む）から設定を構築する。"""
    load_dotenv()
    api_key = os.getenv("KOTOLOG_API_KEY") or None
    return Config(
        model=os.getenv("KOTOLOG_MODEL", DEFAULT_MODEL),
        api_key=api_key,
        db_url=os.getenv("KOTOLOG_DB_URL", DEFAULT_DB_URL),
        default_child=os.getenv("KOTOLOG_DEFAULT_CHILD", DEFAULT_CHILD),
        ollama_base=os.getenv("KOTOLOG_OLLAMA_BASE", DEFAULT_OLLAMA_BASE),
        line_channel_secret=os.getenv("LINE_CHANNEL_SECRET") or None,
        line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or None,
        turso_auth_token=os.getenv("TURSO_AUTH_TOKEN") or None,
        dashboard_token=os.getenv("KOTOLOG_DASHBOARD_TOKEN") or None,
    )
