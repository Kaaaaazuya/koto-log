"""scheduler.py / push.py の単体テスト。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kotolog.line.scheduler import build_daily_summary_text, build_morning_text

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


# ---------------------------------------------------------------------------
# build_daily_summary_text
# ---------------------------------------------------------------------------

_FEEDING = {"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml"}
_DIAPER_POO = {"type": "diaper", "sub_type": "うんち", "amount": None, "unit": None}
_DIAPER_PEE = {"type": "diaper", "sub_type": "おしっこ", "amount": None, "unit": None}
_SLEEP = {"type": "sleep", "sub_type": None, "amount": None, "unit": None}
_TEMP = {"type": "temp", "sub_type": None, "amount": 37.2, "unit": "℃"}


def test_daily_summary_returns_none_when_no_records():
    assert build_daily_summary_text("6/21", []) is None


def test_daily_summary_includes_feeding_count_and_ml():
    records = [_FEEDING, _FEEDING]
    text = build_daily_summary_text("6/21", records)
    assert "授乳: 2回" in text
    assert "240ml" in text


def test_daily_summary_includes_diaper_breakdown():
    records = [_DIAPER_POO, _DIAPER_PEE, _DIAPER_PEE]
    text = build_daily_summary_text("6/21", records)
    assert "おむつ: 3回" in text
    assert "うんち1回" in text
    assert "おしっこ2回" in text


def test_daily_summary_includes_sleep():
    text = build_daily_summary_text("6/21", [_SLEEP])
    assert "睡眠: 1回" in text


def test_daily_summary_includes_max_temp():
    text = build_daily_summary_text("6/21", [_TEMP])
    assert "37.2" in text


def test_daily_summary_skips_missing_types():
    text = build_daily_summary_text("6/21", [_FEEDING])
    assert text is not None
    assert "睡眠" not in text
    assert "おむつ" not in text
