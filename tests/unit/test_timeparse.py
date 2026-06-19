"""T1.2: 相対表現 → JST 絶対時刻 の正規化テスト。"""

from datetime import datetime, timedelta, timezone

from kotolog.utils.timeparse import normalize

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)  # 基準: 6/18 10:00 JST


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_now_like_expressions():
    for text in ("さっき", "今", "今さっき", "", None):
        assert _dt(normalize(text, now=NOW)) == NOW


def test_minutes_and_hours_ago():
    assert _dt(normalize("30分前", now=NOW)) == NOW - timedelta(minutes=30)
    assert _dt(normalize("2時間前", now=NOW)) == NOW - timedelta(hours=2)


def test_numeric_hour_uses_most_recent_past():
    # 3時 は now(10時)より前 → 当日 03:00
    assert _dt(normalize("3時", now=NOW)) == datetime(2026, 6, 18, 3, 0, tzinfo=JST)
    # 15時 は当日まだ未来 → 前日 15:00
    assert _dt(normalize("15時", now=NOW)) == datetime(2026, 6, 17, 15, 0, tzinfo=JST)


def test_numeric_hour_minute():
    assert _dt(normalize("3時30分", now=NOW)) == datetime(2026, 6, 18, 3, 30, tzinfo=JST)


def test_named_periods_use_today():
    assert _dt(normalize("お昼", now=NOW)) == datetime(2026, 6, 18, 12, 0, tzinfo=JST)
    assert _dt(normalize("朝", now=NOW)) == datetime(2026, 6, 18, 7, 0, tzinfo=JST)


def test_iso_passthrough_is_normalized_to_jst():
    out = normalize("2026-06-18T03:00:00+09:00", now=NOW)
    assert _dt(out) == datetime(2026, 6, 18, 3, 0, tzinfo=JST)


def test_output_is_jst_isoformat():
    out = normalize("さっき", now=NOW)
    assert out.endswith("+09:00")
