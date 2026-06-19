"""ツール定義（T1.3）。

LiteLLM/OpenAI 互換の tools スキーマ。enum・必須でできるだけ厳格化し、
小型ローカルLLMでも引数を外しにくくする（Design Doc §7.2）。
モデル非依存：このスキーマと executor はモデルを問わず共通。
"""

from __future__ import annotations

RECORD_TYPES = ["feeding", "sleep", "diaper", "temp"]
# latest は「前回の◯◯はいつ？」用：直近1件＋経過時間を返す
PERIODS = ["today", "yesterday", "last_24h", "last_7days", "latest"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_record",
            "description": "授乳・睡眠・おむつなどの育児記録を1件保存する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": RECORD_TYPES,
                        "description": "記録の種別。",
                    },
                    "sub_type": {
                        "type": "string",
                        "description": "母乳/ミルク/左/右/うんち/おしっこ 等の補足。",
                    },
                    "amount": {"type": "number", "description": "量（例: 120）。"},
                    "unit": {"type": "string", "description": "単位（例: ml）。"},
                    "started_at": {
                        "type": "string",
                        "description": "開始時刻。『さっき』『3時』『お昼』等の相対表現でもよい。",
                    },
                    "ended_at": {
                        "type": "string",
                        "description": "終了時刻（睡眠など区間がある場合）。相対表現可。",
                    },
                    "note": {"type": "string", "description": "自由メモ。"},
                },
                "required": ["type", "started_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_records",
            "description": "期間と種別で記録を集計して返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": RECORD_TYPES,
                        "description": "絞り込む種別。省略時は全種別。",
                    },
                    "sub_type": {
                        "type": "string",
                        "description": "絞り込むサブ種別（母乳/ミルク/うんち 等）。省略可。",
                    },
                    "period": {
                        "type": "string",
                        "enum": PERIODS,
                        "description": "集計期間。『前回はいつ？』には latest（直近1件＋経過時間）を使う。",
                    },
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_or_delete_record",
            "description": "直近の記録を修正または取り消す（『さっきのなし』『150に直して』）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["last"],
                        "description": "対象。現在は直近記録(last)のみ対応。",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["update", "delete"],
                        "description": "修正(update) か 取り消し(delete)。",
                    },
                    "new_values": {
                        "type": "object",
                        "description": "update 時の変更内容（amount/started_at/note 等）。",
                    },
                },
                "required": ["target", "action"],
            },
        },
    },
]
