"""育児記録の型定義。

RecordType / FeedingSubType / DiaperSubType を一元管理する。
ツール定義・サブタイプ正規化・リッチメニューはすべてここを参照する。
"""

from __future__ import annotations

from enum import StrEnum


class RecordType(StrEnum):
    FEEDING = "feeding"
    SLEEP = "sleep"
    DIAPER = "diaper"
    TEMP = "temp"


class FeedingSubType(StrEnum):
    BREAST = "母乳"
    FORMULA = "ミルク"
    PUMPED = "搾母乳"


class DiaperSubType(StrEnum):
    POO = "うんち"
    PEE = "おしっこ"
    BOTH = "両方"
