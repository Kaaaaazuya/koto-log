"""ツール実行（T1.3）。

LLM が選んだ tool 名と引数を受け取り、対応する DB 操作を実行して
構造化結果を返す。結果はエージェント・ループが確認サマリ生成に使う。
時刻の相対表現はここで JST 絶対時刻へ正規化する（Design Doc §6）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from kotolog.db import crud
from kotolog.utils.timeparse import normalize

JST = timezone(timedelta(hours=9))

# new_values 内で時刻として正規化するキー
_TIME_KEYS = ("started_at", "ended_at")


def _record_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


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
        raise ValueError(f"unknown tool: {name}")

    # --- save ---------------------------------------------------------------
    def _save_record(self, args: dict) -> dict:
        started_at = normalize(args["started_at"], now=self.now)
        ended_at = (
            normalize(args["ended_at"], now=self.now) if args.get("ended_at") else None
        )
        rid = crud.insert_record(
            self.conn,
            child_id=self.child_id,
            type=args["type"],
            sub_type=args.get("sub_type"),
            amount=args.get("amount"),
            unit=args.get("unit"),
            started_at=started_at,
            ended_at=ended_at,
            note=args.get("note"),
        )
        return {"ok": True, "action": "save", "record": _record_to_dict(crud.get_record(self.conn, rid))}

    # --- query --------------------------------------------------------------
    def _query_records(self, args: dict) -> dict:
        start, end = self._resolve_period(args["period"])
        rows = crud.query_records(
            self.conn,
            child_id=self.child_id,
            start=start,
            end=end,
            type=args.get("type"),
        )
        total = sum(r["amount"] for r in rows if r["amount"] is not None)
        return {
            "ok": True,
            "action": "query",
            "period": args["period"],
            "type": args.get("type"),
            "count": len(rows),
            "total_amount": total,
            "records": [dict(r) for r in rows],
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
        return {"ok": True, "action": "update", "record": _record_to_dict(crud.get_record(self.conn, rid))}

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
