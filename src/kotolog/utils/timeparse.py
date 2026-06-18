"""時刻正規化（T1.2）。

相対表現（「さっき」「3時」「お昼」「30分前」…）を JST 絶対時刻に変換する。
解決はサーバ側で行い、LLM には絶対時刻で渡す（Design Doc §6）。
認識できない入力は安全側に倒して `now` を返す。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

_NOW_WORDS = {"さっき", "今", "今さっき", "たった今", "いま"}

# 名前付き時間帯 → 当日のその時刻（過去シフトしない）
_NAMED_PERIODS = {
    "朝": 7,
    "お昼": 12,
    "昼": 12,
    "昼間": 12,
    "夕方": 17,
    "夜": 20,
    "深夜": 0,
}

_AGO_RE = re.compile(r"(\d+)\s*(分|時間)前")
_HM_RE = re.compile(r"(\d{1,2})\s*時(?:\s*(\d{1,2})\s*分)?")


def _to_jst(dt: datetime) -> datetime:
    return dt.astimezone(JST)


def normalize(text: str | None, now: datetime | None = None) -> str:
    """相対/絶対の時刻表現を JST 絶対時刻の ISO8601 文字列にする。"""
    now = _to_jst(now) if now else datetime.now(JST)

    if not text or not text.strip():
        return now.isoformat()
    s = text.strip()

    # 1) すでに ISO8601 ならそのまま JST 正規化
    try:
        return _to_jst(datetime.fromisoformat(s)).isoformat()
    except ValueError:
        pass

    # 2) 「今/さっき」系
    if s in _NOW_WORDS:
        return now.isoformat()

    # 3) 「N分前 / N時間前」
    m = _AGO_RE.search(s)
    if m:
        n = int(m.group(1))
        delta = timedelta(minutes=n) if m.group(2) == "分" else timedelta(hours=n)
        return (now - delta).isoformat()

    # 4) 「HH時(MM分)」→ 直近の過去のその時刻
    m = _HM_RE.search(s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        candidate = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate.isoformat()

    # 5) 名前付き時間帯 → 当日のその時刻
    for word, hour in _NAMED_PERIODS.items():
        if word in s:
            return now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    # 6) 認識不能 → now（安全側）
    return now.isoformat()
