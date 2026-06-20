"""scheduler.py / push.py の単体テスト。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kotolog.line.scheduler import build_morning_text

# ---------------------------------------------------------------------------
# FakeLLMClient（scheduler テスト用）
# ---------------------------------------------------------------------------


def _fake_llm(text: str):
    msg = MagicMock()
    msg.content = text
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]

    client = MagicMock()
    client.complete.return_value = resp
    return client


# ---------------------------------------------------------------------------
# build_morning_text
# ---------------------------------------------------------------------------


def test_build_morning_text_many_days():
    llm = _fake_llm("体を大切に過ごして。")
    result = build_morning_text(30, llm)
    assert "30 日" in result
    assert "体を大切に過ごして。" in result


def test_build_morning_text_one_day():
    llm = _fake_llm("いよいよ明日だ。")
    result = build_morning_text(1, llm)
    assert "明日が予定日" in result


def test_build_morning_text_zero_days():
    llm = _fake_llm("今日かもしれない。")
    result = build_morning_text(0, llm)
    assert "今日が予定日" in result


def test_llm_is_called_once():
    llm = _fake_llm("応援してる。")
    build_morning_text(10, llm)
    llm.complete.assert_called_once()


# ---------------------------------------------------------------------------
# send_push
# ---------------------------------------------------------------------------


def test_send_push_posts_correct_payload():
    with patch("kotolog.line.push.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        from kotolog.line.push import send_push

        send_push("U123", "おはよう！", "token-abc")

        mock_client.post.assert_called_once()
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["to"] == "U123"
        assert kwargs["json"]["messages"][0]["text"] == "おはよう！"
        assert "token-abc" in kwargs["headers"]["Authorization"]


# ---------------------------------------------------------------------------
# _run_morning_push（条件分岐）
# ---------------------------------------------------------------------------


def _base_cfg():
    from kotolog.config import Config

    return Config(
        model="m",
        api_key=None,
        db_url=":memory:",
        default_child="baby",
        ollama_base="http://localhost:11434",
        line_channel_secret=None,
        line_channel_access_token="tok",
        turso_auth_token=None,
        dashboard_token=None,
    )


def test_run_morning_push_skips_when_no_due_date(monkeypatch):
    """due_date が DB に未設定の場合は push しない。"""
    monkeypatch.setattr("kotolog.line.scheduler.load_config", _base_cfg)

    with (
        patch("kotolog.line.scheduler.connect") as mock_connect,
        patch("kotolog.line.scheduler.crud.get_setting", return_value=None),
        patch("kotolog.line.push.httpx.Client") as mock_http,
    ):
        mock_connect.return_value = MagicMock()
        from kotolog.line.scheduler import _run_morning_push

        _run_morning_push()
        mock_http.assert_not_called()


def test_run_morning_push_skips_when_past_due(monkeypatch):
    """予定日を過ぎた場合は push しない。"""
    monkeypatch.setattr("kotolog.line.scheduler.load_config", _base_cfg)

    def _get_setting(_conn, key):
        return "2020-01-01" if key == "due_date" else "U123"

    with (
        patch("kotolog.line.scheduler.connect") as mock_connect,
        patch("kotolog.line.scheduler.crud.get_setting", side_effect=_get_setting),
        patch("kotolog.line.push.httpx.Client") as mock_http,
    ):
        mock_connect.return_value = MagicMock()
        from kotolog.line.scheduler import _run_morning_push

        _run_morning_push()
        mock_http.assert_not_called()
