"""T1.9: sub_type 正規化のテスト。"""

import pytest

from kotolog.utils.subtype import normalize_sub_type


@pytest.mark.parametrize(
    "type_, raw, expected",
    [
        ("feeding", "おっぱい", "母乳"),
        ("feeding", "母乳", "母乳"),
        ("feeding", "粉ミルク", "ミルク"),
        ("feeding", "人工乳", "ミルク"),
        ("feeding", "搾乳", "搾母乳"),
        ("diaper", "うんP", "うんち"),
        ("diaper", "便", "うんち"),
        ("diaper", "尿", "おしっこ"),
        ("diaper", "うんちとおしっこ", "両方"),
    ],
)
def test_normalizes_synonyms(type_, raw, expected):
    assert normalize_sub_type(type_, raw) == expected


def test_trims_whitespace():
    assert normalize_sub_type("feeding", "  おっぱい ") == "母乳"


def test_unknown_value_passes_through():
    # 表記ゆれ辞書に無い値は情報を落とさずそのまま返す
    assert normalize_sub_type("sleep", "昼寝") == "昼寝"
    assert normalize_sub_type("feeding", "謎の値") == "謎の値"


def test_none_and_empty():
    assert normalize_sub_type("feeding", None) is None
    assert normalize_sub_type("feeding", "   ") is None


def test_unknown_type_passes_value_through():
    assert normalize_sub_type(None, "おっぱい") == "おっぱい"
