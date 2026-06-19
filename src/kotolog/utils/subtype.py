"""sub_type 正規化（T1.9）。

保存時に表記ゆれ（母乳/おっぱい、粉ミルク/ミルク、うんち/便 …）を正規形へ寄せる。
集計（by_sub_type）がカテゴリのブレで割れないようにするための前処理。
辞書に無い値は情報を落とさずそのまま返す（自由入力の強みを殺さない）。
"""

from __future__ import annotations

from kotolog.types import DiaperSubType, FeedingSubType, RecordType

# 種別ごとの「正規形 → 同義語」。
_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    RecordType.FEEDING: {
        FeedingSubType.BREAST: ("母乳", "おっぱい", "直母"),
        FeedingSubType.FORMULA: ("ミルク", "粉ミルク", "人工乳"),
        FeedingSubType.PUMPED: ("搾母乳", "搾乳", "さく乳"),
    },
    RecordType.DIAPER: {
        DiaperSubType.POO: ("うんち", "うんP", "うんp", "便", "排便", "うんこ"),
        DiaperSubType.PEE: ("おしっこ", "尿"),
        DiaperSubType.BOTH: ("両方", "うんちとおしっこ"),
    },
}

# 反転インデックス: {type: {同義語(小文字): 正規形}}
_REVERSE: dict[str, dict[str, str]] = {
    t: {syn.lower(): canon for canon, syns in table.items() for syn in syns} for t, table in _SYNONYMS.items()
}


def normalize_sub_type(type: str | None, sub_type: str | None) -> str | None:
    """`type` の文脈で sub_type を正規形へ寄せる。未知値はそのまま返す。"""
    if sub_type is None:
        return None
    value = sub_type.strip()
    if not value:
        return None
    table = _REVERSE.get(type or "")
    if table:
        canon = table.get(value.lower())
        if canon:
            return canon
    return value
