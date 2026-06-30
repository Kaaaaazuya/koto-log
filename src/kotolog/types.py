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
    BABY_FOOD = "baby_food"
    BATH = "bath"
    MEDICINE = "medicine"
    HOSPITAL = "hospital"
    OUTING = "outing"
    HEIGHT = "height"
    WEIGHT = "weight"


class FeedingSubType(StrEnum):
    BREAST = "母乳"
    FORMULA = "ミルク"
    PUMPED = "搾母乳"


class DiaperSubType(StrEnum):
    POO = "うんち"
    PEE = "おしっこ"
    BOTH = "両方"


RECORD_TYPE_LABELS: dict[str, str] = {
    RecordType.FEEDING: "授乳",
    RecordType.SLEEP: "睡眠",
    RecordType.DIAPER: "おむつ",
    RecordType.TEMP: "体温",
    RecordType.BABY_FOOD: "離乳食",
    RecordType.BATH: "お風呂",
    RecordType.MEDICINE: "薬",
    RecordType.HOSPITAL: "病院",
    RecordType.OUTING: "外出",
    RecordType.HEIGHT: "身長",
    RecordType.WEIGHT: "体重",
}
