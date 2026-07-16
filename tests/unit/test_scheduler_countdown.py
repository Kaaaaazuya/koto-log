"""Issue #99: 朝の出産カウントダウンの残り日数を JST 基準で計算する。

`date.today()`（サーバーTZ=UTC）ではなく `datetime.now(JST).date()` を使うことで、
朝 7:00 JST（= 前日 22:00 UTC）に走るジョブでも残り日数が 1 日ずれないことを固定する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from kotolog.config import Config

JST = timezone(timedelta(hours=9))

# UTC では前日（2026-07-16）だが JST では当日（2026-07-17）になる境界時刻。
BOUNDARY_UTC = datetime(2026, 7, 16, 22, 30, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """`now()` だけ境界時刻に固定し、tz 変換は本物の astimezone を通す。"""

    @classmethod
    def now(cls, tz=None):
        return BOUNDARY_UTC.astimezone(tz) if tz is not None else BOUNDARY_UTC.replace(tzinfo=None)


def _base_cfg() -> Config:
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


def _run_with_due_date(due_date_str: str):
    """境界時刻に固定した状態で _run_morning_push を走らせ、build_morning_text の呼び出しを返す。"""
    from kotolog.line import scheduler

    def _get_setting(_conn, key):
        return due_date_str if key == "due_date" else None

    with (
        patch.object(scheduler, "load_config", _base_cfg),
        patch.object(scheduler, "datetime", _FrozenDatetime),
        patch.object(scheduler, "connect"),
        patch.object(scheduler.crud, "get_setting", side_effect=_get_setting),
        patch.object(scheduler, "build_morning_text", return_value="テスト") as mock_build,
        patch.object(scheduler, "_fanout_push"),
        patch("kotolog.llm.client.LLMClient"),
        patch("kotolog.obs.usage.new_trace_id"),
        patch("kotolog.obs.usage.sink_from_config"),
    ):
        scheduler._run_morning_push()
    return mock_build


def test_countdown_uses_jst_date_at_utc_boundary():
    """JST では当日なので、予定日が今日(JST)なら残り 0 日。UTC 基準だと 1 日になってしまう。"""
    mock_build = _run_with_due_date("2026-07-17")  # JST の「今日」
    mock_build.assert_called_once()
    assert mock_build.call_args[0][0] == 0


def test_countdown_future_due_date_at_utc_boundary():
    """予定日が 3 日先なら残り 3 日（JST 基準）。"""
    mock_build = _run_with_due_date("2026-07-20")
    mock_build.assert_called_once()
    assert mock_build.call_args[0][0] == 3
