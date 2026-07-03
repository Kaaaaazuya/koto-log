"""ツール実行（T1.3）。

LLM が選んだ tool 名と引数を受け取り、対応する DB 操作を実行して
構造化結果を返す。結果はエージェント・ループが確認サマリ生成に使う。
時刻の相対表現はここで JST 絶対時刻へ正規化する（Design Doc §6）。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from kotolog.db import crud
from kotolog.tools.definitions import CONFIG_KEYS
from kotolog.utils.subtype import normalize_sub_type
from kotolog.utils.timeparse import normalize

JST = timezone(timedelta(hours=9))

# new_values 内で時刻として正規化するキー
_TIME_KEYS = ("started_at", "ended_at")


def _record_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _aggregate(rows: list[sqlite3.Row], key: str) -> dict:
    """rows を key（type / sub_type）ごとに件数・合計でまとめる。None キーは除外。"""
    out: dict[str, dict] = {}
    for r in rows:
        k = r[key]
        if k is None:
            continue
        bucket = out.setdefault(k, {"count": 0, "total_amount": 0})
        bucket["count"] += 1
        if r["amount"] is not None:
            bucket["total_amount"] += r["amount"]
    return out


class ToolExecutor:
    def __init__(
        self,
        conn: sqlite3.Connection,
        child_id: int,
        now: datetime | None = None,
    ) -> None:
        self.conn = conn
        self.child_id = child_id
        self.now = now or datetime.now(JST)

    def execute(self, name: str, args: dict) -> dict:
        if name == "save_record":
            return self._save_record(args)
        if name == "query_records":
            return self._query_records(args)
        if name == "update_or_delete_record":
            return self._update_or_delete(args)
        if name == "set_config":
            return self._set_config(args)
        raise ValueError(f"unknown tool: {name}")

    # --- save ---------------------------------------------------------------
    def _save_record(self, args: dict) -> dict:
        started_at = normalize(args["started_at"], now=self.now)
        ended_at = normalize(args["ended_at"], now=self.now) if args.get("ended_at") else None
        sub_type = normalize_sub_type(args["type"], args.get("sub_type"))
        rid = crud.insert_record(
            self.conn,
            child_id=self.child_id,
            type=args["type"],
            sub_type=sub_type,
            amount=args.get("amount"),
            unit=args.get("unit"),
            started_at=started_at,
            ended_at=ended_at,
            note=args.get("note"),
        )
        return {
            "ok": True,
            "action": "save",
            "record": _record_to_dict(crud.get_record(self.conn, rid)),
        }

    # --- query --------------------------------------------------------------
    def _query_records(self, args: dict) -> dict:
        type_filter = args.get("type")
        if args["period"] == "latest":
            return self._latest(type_filter)
        start, end = self._resolve_period(args["period"])
        sub_type_filter = normalize_sub_type(type_filter, args["sub_type"]) if args.get("sub_type") else None
        rows = crud.query_records(
            self.conn,
            child_id=self.child_id,
            start=start,
            end=end,
            type=type_filter,
            sub_type=sub_type_filter,
        )
        total = sum(r["amount"] for r in rows if r["amount"] is not None)
        return {
            "ok": True,
            "action": "query",
            "period": args["period"],
            "type": type_filter,
            "sub_type": sub_type_filter,
            "count": len(rows),
            "total_amount": total,
            "by_type": _aggregate(rows, "type"),
            "by_sub_type": _aggregate(rows, "sub_type"),
            "records": [dict(r) for r in rows],
        }

    def _latest(self, type_filter: str | None) -> dict:
        """直近1件と経過時間（分/時間）を返す（「前回の◯◯いつ？」用）。"""
        rec = crud.get_last_record(self.conn, self.child_id, type=type_filter)
        if rec is None:
            return {"ok": False, "action": "latest", "type": type_filter, "reason": "no_record"}
        elapsed_min = (self.now - datetime.fromisoformat(rec["started_at"])).total_seconds() / 60
        return {
            "ok": True,
            "action": "latest",
            "type": type_filter,
            "record": _record_to_dict(rec),
            "elapsed_minutes": int(elapsed_min),
            "elapsed_hours": round(elapsed_min / 60, 1),
        }

    # --- update / delete ----------------------------------------------------
    def _update_or_delete(self, args: dict) -> dict:
        # 現在 target は last のみ（definitions の enum と一致）
        last = crud.get_last_record(self.conn, self.child_id)
        if last is None:
            return {"ok": False, "reason": "no_record"}

        rid = last["id"]
        if args["action"] == "delete":
            crud.delete_record(self.conn, rid)
            return {"ok": True, "action": "delete", "record": _record_to_dict(last)}

        new_values = dict(args.get("new_values") or {})
        for key in _TIME_KEYS:
            if new_values.get(key):
                new_values[key] = normalize(new_values[key], now=self.now)
        updated = crud.update_record(self.conn, rid, new_values)
        if not updated:
            return {"ok": False, "reason": "no_change"}
        return {
            "ok": True,
            "action": "update",
            "record": _record_to_dict(crud.get_record(self.conn, rid)),
        }

    # --- config -------------------------------------------------------------
    def _set_config(self, args: dict) -> dict:
        key, value = args["key"], args["value"]
        # Issue #30: Validate that only permitted config keys can be set
        if key not in CONFIG_KEYS:
            return {"ok": False, "reason": f"Invalid config key: {key}. Allowed keys: {', '.join(CONFIG_KEYS)}"}
        if key == "due_date":
            try:
                date.fromisoformat(value)
            except ValueError:
                return {"ok": False, "reason": f"日付の形式が正しくない: {value}。YYYY-MM-DD で指定して"}
        crud.set_setting(self.conn, key, value)
        return {"ok": True, "key": key, "value": value}

    # --- helpers ------------------------------------------------------------
    def _resolve_period(self, period: str) -> tuple[str, str]:
        now = self.now
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today":
            return day_start.isoformat(), now.isoformat()
        if period == "yesterday":
            y = day_start - timedelta(days=1)
            end = day_start - timedelta(seconds=1)
            return y.isoformat(), end.isoformat()
        if period == "last_24h":
            return (now - timedelta(hours=24)).isoformat(), now.isoformat()
        if period == "last_7days":
            return (now - timedelta(days=7)).isoformat(), now.isoformat()
        raise ValueError(f"unknown period: {period}")
