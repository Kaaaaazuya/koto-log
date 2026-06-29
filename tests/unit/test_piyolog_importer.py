"""ぴよログ テキストエクスポートのパーサーテスト（P: piyolog-analytics-migration）。

LLM・DB には一切触れない純粋関数のみ検証する。
"""

from __future__ import annotations

import pytest

from kotolog.importer.piyolog import ParsedRecord, parse_piyolog
from kotolog.types import DiaperSubType, FeedingSubType, RecordType

# ---- サンプルデータ -------------------------------------------------------

SAMPLE_TSV = """\
2024/01/01 06:30\tミルク\t120ml
2024/01/01 08:00\tおしっこ\t
2024/01/01 09:00\t睡眠\t
2024/01/01 10:30\t起床\t
2024/01/01 12:00\t母乳\t左5分右8分
2024/01/01 15:00\t体温\t36.5
2024/01/01 16:00\tうんち\t
"""

SAMPLE_HEADER = """\
----- 2024年 1月 1日(月) -----

  06:30  ミルク  120ml
  08:00  おしっこ
  09:00  睡眠
  10:30  起床
  12:00  母乳  左5分
  15:00  体温  36.5
"""

SAMPLE_MULTIDAY_HEADER = """\
----- 2024年 1月 1日(月) -----

  22:00  睡眠

----- 2024年 1月 2日(火) -----

  06:00  起床
  08:00  ミルク  80
"""

# ---- フォーマットA（タブ区切り）------------------------------------------


def test_tsv_record_count():
    records = parse_piyolog(SAMPLE_TSV)
    # milk, pee, sleep(with end), breast, temp, poo = 6
    assert len(records) == 6


def test_tsv_milk():
    records = parse_piyolog(SAMPLE_TSV)
    milk = next(r for r in records if r.sub_type == FeedingSubType.FORMULA)
    assert milk.type == RecordType.FEEDING
    assert milk.amount == 120.0
    assert milk.unit == "ml"
    assert milk.started_at == "2024-01-01T06:30:00+09:00"


def test_tsv_diaper_pee():
    records = parse_piyolog(SAMPLE_TSV)
    pee = next(r for r in records if r.type == RecordType.DIAPER and r.sub_type == DiaperSubType.PEE)
    assert pee.started_at == "2024-01-01T08:00:00+09:00"


def test_tsv_sleep_pairing():
    records = parse_piyolog(SAMPLE_TSV)
    sleep = next(r for r in records if r.type == RecordType.SLEEP)
    assert sleep.started_at == "2024-01-01T09:00:00+09:00"
    assert sleep.ended_at == "2024-01-01T10:30:00+09:00"


def test_tsv_breast():
    records = parse_piyolog(SAMPLE_TSV)
    breast = next(r for r in records if r.sub_type == FeedingSubType.BREAST)
    assert breast.type == RecordType.FEEDING
    assert breast.note == "左5分右8分"


def test_tsv_temp():
    records = parse_piyolog(SAMPLE_TSV)
    temp = next(r for r in records if r.type == RecordType.TEMP)
    assert temp.amount == 36.5
    assert temp.unit == "℃"


def test_tsv_diaper_poo():
    records = parse_piyolog(SAMPLE_TSV)
    poo = next(r for r in records if r.sub_type == DiaperSubType.POO)
    assert poo.type == RecordType.DIAPER


# ---- フォーマットB（日付ヘッダー＋時刻のみ行）-----------------------------


def test_header_format_record_count():
    records = parse_piyolog(SAMPLE_HEADER)
    # milk, pee, sleep, breast, temp = 5
    assert len(records) == 5


def test_header_format_milk_timestamp():
    records = parse_piyolog(SAMPLE_HEADER)
    milk = next(r for r in records if r.sub_type == FeedingSubType.FORMULA)
    assert milk.started_at == "2024-01-01T06:30:00+09:00"
    assert milk.amount == 120.0


def test_header_format_sleep_paired():
    records = parse_piyolog(SAMPLE_HEADER)
    sleep = next(r for r in records if r.type == RecordType.SLEEP)
    assert sleep.ended_at == "2024-01-01T10:30:00+09:00"


# ---- 日付をまたぐ睡眠 ----------------------------------------------------


def test_cross_midnight_sleep():
    records = parse_piyolog(SAMPLE_MULTIDAY_HEADER)
    sleep = next(r for r in records if r.type == RecordType.SLEEP)
    assert sleep.started_at == "2024-01-01T22:00:00+09:00"
    assert sleep.ended_at == "2024-01-02T06:00:00+09:00"

    milk = next(r for r in records if r.type == RecordType.FEEDING)
    assert milk.started_at == "2024-01-02T08:00:00+09:00"


# ---- エッジケース --------------------------------------------------------


def test_orphan_wake_is_ignored():
    records = parse_piyolog("2024/01/01 06:00\t起床\t\n")
    assert records == []


def test_unclosed_sleep_is_included():
    records = parse_piyolog("2024/01/01 22:00\t睡眠\t\n")
    assert len(records) == 1
    assert records[0].type == RecordType.SLEEP
    assert records[0].ended_at is None


def test_unknown_category_skipped():
    records = parse_piyolog("2024/01/01 10:00\t入浴\t\n")
    assert records == []


def test_empty_input():
    assert parse_piyolog("") == []


def test_blank_lines_ignored():
    text = "\n\n2024/01/01 08:00\tおしっこ\t\n\n"
    records = parse_piyolog(text)
    assert len(records) == 1


def test_diaper_from_detail_poo():
    records = parse_piyolog("2024/01/01 10:00\tおむつ\tうんち\n")
    assert records[0].sub_type == DiaperSubType.POO


def test_diaper_from_detail_both():
    records = parse_piyolog("2024/01/01 10:00\tおむつ\t両方\n")
    assert records[0].sub_type == DiaperSubType.BOTH


def test_fullwidth_amount():
    records = parse_piyolog("2024/01/01 06:30\tミルク\t１２０ml\n")
    assert records[0].amount == 120.0


def test_sorted_by_started_at():
    text = (
        "2024/01/01 12:00\tうんち\t\n"
        "2024/01/01 08:00\tおしっこ\t\n"
        "2024/01/01 10:00\tミルク\t100ml\n"
    )
    records = parse_piyolog(text)
    times = [r.started_at for r in records]
    assert times == sorted(times)
