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
DEFAULT_USER_MSG_LIMIT = 100  # messages per hour per user
DEFAULT_USER_LLM_LIMIT = 50  # LLM calls per hour per user


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
    usage_log: bool = False
    user_msg_limit: int = DEFAULT_USER_MSG_LIMIT
    user_llm_limit: int = DEFAULT_USER_LLM_LIMIT


def load_config() -> Config:
    """環境変数（.env 含む）から設定を構築する。

    Issue #31: LINE webhook を使用する場合、必須の環境変数をチェックする。
    """
    load_dotenv()
    api_key = os.getenv("KOTOLOG_API_KEY") or None

    def _get_int(key: str, default: int) -> int:
        val = os.getenv(key)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            return default

    line_channel_secret = os.getenv("LINE_CHANNEL_SECRET") or None
    line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or None

    # Issue #31: LINE webhook 使用時に必須env varをチェック
    # LINE webhook を動作させるには両者が必要（署名検証・返信送信）
    if not line_channel_secret or not line_channel_access_token:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "LINE webhook is disabled: LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN must both be set. "
            "Set both environment variables to enable LINE message handling."
        )

    return Config(
        model=os.getenv("KOTOLOG_MODEL", DEFAULT_MODEL),
        api_key=api_key,
        db_url=os.getenv("KOTOLOG_DB_URL", DEFAULT_DB_URL),
        default_child=os.getenv("KOTOLOG_DEFAULT_CHILD", DEFAULT_CHILD),
        ollama_base=os.getenv("KOTOLOG_OLLAMA_BASE", DEFAULT_OLLAMA_BASE),
        line_channel_secret=line_channel_secret,
        line_channel_access_token=line_channel_access_token,
        turso_auth_token=os.getenv("TURSO_AUTH_TOKEN") or None,
        dashboard_token=os.getenv("KOTOLOG_DASHBOARD_TOKEN") or None,
        usage_log=os.getenv("KOTOLOG_USAGE_LOG", "").lower() in ("1", "true", "yes"),
        user_msg_limit=_get_int("KOTOLOG_USER_MSG_LIMIT_PER_HOUR", DEFAULT_USER_MSG_LIMIT),
        user_llm_limit=_get_int("KOTOLOG_USER_LLM_LIMIT_PER_HOUR", DEFAULT_USER_LLM_LIMIT),
    )
