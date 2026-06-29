"""ぴよログ テキストエクスポートのパーサー。

ぴよログの「テキスト形式バックアップ」を parse して koto-log の insert_record に
渡せる dict のリストへ変換する。LLM・DB には一切触れない純粋関数。

対応フォーマット
  A: YYYY/MM/DD HH:MM<TAB>種別<TAB>詳細  (1行1記録・日付付き)
  B: ----- YYYY年 M月 D日(曜) -----  ヘッダー行 + HH:MM  種別  詳細

対応種別
  授乳系: ミルク / 母乳 / 搾母乳 → RecordType.FEEDING
  おむつ系: おしっこ / うんち / おむつ → RecordType.DIAPER
  睡眠: 睡眠 / 起床 → RecordType.SLEEP (start/end ペアリング)
  体温: 体温 → RecordType.TEMP
  その他: スキップ
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kotolog.types import DiaperSubType, FeedingSubType, RecordType

JST = timezone(timedelta(hours=9))

# 2024/01/01 06:30 または 2024-01-01 06:30
_FULL_DT_RE = re.compile(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\s+(\d{1,2}):(\d{2})")

# ----- 2024年 1月 1日(月) ----- （スペース・カッコの有無を問わない）
_DATE_HEADER_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")

# 行頭の時刻のみ: 06:30
_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})")

# 数値を取り出す（全角数字は事前に正規化する）
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)")

# 全角数字・小数点 → 半角
_FULLWIDTH_TABLE = str.maketrans("０１２３４５６７８９．", "0123456789.")


@dataclass
class ParsedRecord:
    type: str
    started_at: str  # JST ISO8601
    sub_type: str | None = None
    amount: float | None = None
    unit: str | None = None
    ended_at: str | None = None
    note: str | None = None


def _iso(year: int, month: int, day: int, hour: int, minute: int) -> str:
    return datetime(year, month, day, hour, minute, tzinfo=JST).isoformat()


def _parse_amount(s: str) -> float | None:
    s = s.translate(_FULLWIDTH_TABLE)
    m = _AMOUNT_RE.search(s)
    return float(m.group(1)) if m else None


def _split(rest: str) -> tuple[str, str]:
    """rest をタブ優先、次に2+スペースで種別と詳細に分割する。"""
    if "\t" in rest:
        parts = rest.split("\t", 1)
    else:
        parts = re.split(r"\s{2,}", rest, 1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def _diaper_sub(detail: str) -> str | None:
    if "両方" in detail or ("うんち" in detail and "おしっこ" in detail):
        return DiaperSubType.BOTH
    if "うんち" in detail:
        return DiaperSubType.POO
    if "おしっこ" in detail:
        return DiaperSubType.PEE
    return None


# sentinel for wake-up events (not a real RecordType)
_WAKE = "__wake__"


def _classify(category: str, detail: str) -> ParsedRecord | None:
    cat = category.strip()
    det = detail.strip()

    # --- 授乳 ---
    if cat in ("ミルク", "粉ミルク", "人工乳", "フォローアップミルク"):
        a = _parse_amount(det)
        return ParsedRecord(RecordType.FEEDING, "", FeedingSubType.FORMULA, a, "ml" if a else None)
    if cat in ("母乳", "授乳", "おっぱい", "直母", "授乳（母乳）", "母乳(左)", "母乳(右)"):
        return ParsedRecord(RecordType.FEEDING, "", FeedingSubType.BREAST, note=det or None)
    if cat in ("搾母乳", "搾乳", "さく乳"):
        a = _parse_amount(det)
        return ParsedRecord(RecordType.FEEDING, "", FeedingSubType.PUMPED, a, "ml" if a else None)

    # --- おむつ ---
    if cat == "おしっこ":
        return ParsedRecord(RecordType.DIAPER, "", DiaperSubType.PEE)
    if cat in ("うんち", "排便"):
        return ParsedRecord(RecordType.DIAPER, "", DiaperSubType.POO)
    if cat in ("おむつ", "おむつ交換"):
        return ParsedRecord(RecordType.DIAPER, "", _diaper_sub(det))

    # --- 睡眠 ---
    if cat in ("睡眠", "ねんね", "就寝", "昼寝", "夜間睡眠", "お昼寝"):
        return ParsedRecord(RecordType.SLEEP, "")
    if cat in ("起床", "目覚め", "起きた", "起床・目覚め"):
        return ParsedRecord(_WAKE, "")

    # --- 体温 ---
    if cat == "体温":
        a = _parse_amount(det)
        return ParsedRecord(RecordType.TEMP, "", amount=a, unit="℃" if a else None)

    return None


def _handle(rec: ParsedRecord, records: list[ParsedRecord], pending: list[ParsedRecord]) -> None:
    if rec.type == _WAKE:
        if pending:
            sleep = pending.pop()
            sleep.ended_at = rec.started_at
            records.append(sleep)
        # 対応する睡眠開始がなければ orphan として無視
    elif rec.type == RecordType.SLEEP:
        pending.append(rec)
    else:
        records.append(rec)


def parse_piyolog(text: str) -> list[ParsedRecord]:
    """ぴよログ テキストエクスポートをパースして記録リストを返す。

    DB や LLM には触れない純粋関数。
    """
    records: list[ParsedRecord] = []
    pending_sleeps: list[ParsedRecord] = []
    current_date: tuple[int, int, int] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # フォーマット A: 行頭に日付+時刻
        m = _FULL_DT_RE.match(line)
        if m:
            y, mo, d, h, mi = (int(x) for x in m.groups())
            current_date = (y, mo, d)
            rest = line[m.end():].strip()
            cat, det = _split(rest)
            rec = _classify(cat, det)
            if rec is not None:
                rec.started_at = _iso(y, mo, d, h, mi)
                _handle(rec, records, pending_sleeps)
            continue

        # フォーマット B ヘッダー行: ----- 2024年 1月 1日(月) -----
        dh = _DATE_HEADER_RE.search(line)
        if dh:
            current_date = (int(dh.group(1)), int(dh.group(2)), int(dh.group(3)))
            continue

        # フォーマット B レコード行: HH:MM  種別  詳細
        if current_date is not None:
            m2 = _TIME_ONLY_RE.match(line)
            if m2:
                h, mi = int(m2.group(1)), int(m2.group(2))
                rest = line[m2.end():].strip()
                cat, det = _split(rest)
                rec = _classify(cat, det)
                if rec is not None:
                    rec.started_at = _iso(*current_date, h, mi)
                    _handle(rec, records, pending_sleeps)

    # 閉じられなかった睡眠（エクスポート時点で継続中）
    records.extend(pending_sleeps)
    records.sort(key=lambda r: r.started_at)
    return records
