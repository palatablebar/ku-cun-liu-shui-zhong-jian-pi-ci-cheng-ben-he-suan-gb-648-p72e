"""SQLite 存储：批次、流水行、FIFO 层、消耗段、盘点记录与运行日志。

约定：
- 数量、单价、金额一律存 Decimal 字符串，不落 float；
- 物料编码、单据号按字符串保形，前导零不过 int()；
- 一次运行的所有行在同一个事务里写入，失败整体回滚；
- 同一 run_id 重跑先删后插，行数、合计与首跑一致。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    movements_sha256 TEXT NOT NULL DEFAULT '',
    opening_sha256 TEXT NOT NULL DEFAULT '',
    counts_sha256 TEXT NOT NULL DEFAULT '',
    movements_file TEXT NOT NULL DEFAULT '',
    opening_file TEXT NOT NULL DEFAULT '',
    counts_file TEXT NOT NULL DEFAULT '',
    movements_bytes INTEGER NOT NULL DEFAULT 0,
    opening_bytes INTEGER NOT NULL DEFAULT 0,
    counts_bytes INTEGER NOT NULL DEFAULT 0,
    raw_rows INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    superseded INTEGER NOT NULL DEFAULT 0,
    duplicate INTEGER NOT NULL DEFAULT 0,
    blank INTEGER NOT NULL DEFAULT 0,
    segments INTEGER NOT NULL DEFAULT 0,
    opening_qty TEXT NOT NULL DEFAULT '0',
    in_qty TEXT NOT NULL DEFAULT '0',
    out_qty TEXT NOT NULL DEFAULT '0',
    adj_in_qty TEXT NOT NULL DEFAULT '0',
    adj_out_qty TEXT NOT NULL DEFAULT '0',
    ending_qty TEXT NOT NULL DEFAULT '0',
    cogs TEXT NOT NULL DEFAULT '0',
    ending_value TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL DEFAULT 'pending',
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_log ON run_log(run_id, seq);
CREATE TABLE IF NOT EXISTS movements (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    row_no INTEGER NOT NULL,
    doc_no TEXT NOT NULL DEFAULT '',
    line_no INTEGER NOT NULL DEFAULT 0,
    doc_type TEXT NOT NULL DEFAULT '',
    warehouse TEXT NOT NULL DEFAULT '',
    sku TEXT NOT NULL DEFAULT '',
    qty TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    unit_factor TEXT NOT NULL DEFAULT '1',
    qty_kg TEXT NOT NULL DEFAULT '',
    price TEXT NOT NULL DEFAULT '',
    ts_utc TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    ref_doc TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'valid',
    reason TEXT NOT NULL DEFAULT '',
    flags_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '[]',
    raw_qty TEXT NOT NULL DEFAULT '',
    raw_unit TEXT NOT NULL DEFAULT '',
    raw_cost TEXT NOT NULL DEFAULT '',
    raw_ts TEXT NOT NULL DEFAULT '',
    applied_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_movements_run ON movements(run_id, status, seq);
CREATE TABLE IF NOT EXISTS layers (
    run_id TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    origin TEXT NOT NULL,
    doc_no TEXT NOT NULL DEFAULT '',
    line_no INTEGER NOT NULL DEFAULT 0,
    created_ts TEXT NOT NULL DEFAULT '',
    unit_cost TEXT NOT NULL DEFAULT '',
    qty_in TEXT NOT NULL DEFAULT '',
    qty_remaining TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_layers_run ON layers(run_id, sku, warehouse);
CREATE TABLE IF NOT EXISTS consumptions (
    run_id TEXT NOT NULL,
    seg_id TEXT NOT NULL,
    out_doc TEXT NOT NULL DEFAULT '',
    out_line INTEGER NOT NULL DEFAULT 0,
    sku TEXT NOT NULL DEFAULT '',
    warehouse TEXT NOT NULL DEFAULT '',
    layer_id TEXT NOT NULL DEFAULT '',
    qty TEXT NOT NULL DEFAULT '',
    unit_cost TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_consumptions_run ON consumptions(run_id, out_doc);
CREATE TABLE IF NOT EXISTS counts (
    run_id TEXT NOT NULL,
    count_no TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    sku TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    counted_qty TEXT NOT NULL DEFAULT '',
    system_qty TEXT NOT NULL DEFAULT '',
    diff_qty TEXT NOT NULL DEFAULT '',
    adjust_qty TEXT NOT NULL DEFAULT '',
    adjust_value TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_counts_run ON counts(run_id, count_no);
CREATE TABLE IF NOT EXISTS count_state (
    count_no TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    payload_sha TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

def _bind(value):
    """SQLite 只认 str/int/float/None：Decimal 一律转字符串保存。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


RUN_COLUMNS = (
    "run_id", "created_at", "rule_version", "params_json", "movements_sha256",
    "opening_sha256", "counts_sha256", "movements_file", "opening_file", "counts_file",
    "movements_bytes", "opening_bytes", "counts_bytes", "raw_rows", "applied", "quarantined",
    "superseded", "duplicate", "blank", "segments", "opening_qty", "in_qty", "out_qty",
    "adj_in_qty", "adj_out_qty", "ending_qty", "cogs", "ending_value", "status", "duration_ms",
)


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- 批次 ----------
    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["params"] = json.loads(data.pop("params_json") or "{}")
        return data

    def latest_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return self._run_row(row) if row else None

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def save_run(self, run: dict, movements: list[dict], layers: list[dict],
                 consumptions: list[dict], counts: list[dict],
                 log: list[dict]) -> None:
        params_json = json.dumps(run.get("params", {}), ensure_ascii=False, sort_keys=True)
        run_id = run["run_id"]
        with self._conn() as conn:
            for table in ("movements", "layers", "consumptions", "counts", "run_log"):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            conn.execute(
                f"""INSERT OR REPLACE INTO runs ({", ".join(RUN_COLUMNS)})
                VALUES ({", ".join("?" for _ in RUN_COLUMNS)})""",
                tuple([run_id, run["created_at"], run["rule_version"], params_json]
                      + [_bind(run.get(
                          column, "" if column.endswith(("sha256", "file")) else 0))
                         for column in RUN_COLUMNS[4:]]),
            )
            conn.executemany(
                """INSERT INTO movements (run_id, seq, row_no, doc_no, line_no, doc_type,
                    warehouse, sku, qty, unit, unit_factor, qty_kg, price, ts_utc, direction,
                    ref_doc, revision, status, reason, flags_json, raw_json, raw_qty, raw_unit,
                    raw_cost, raw_ts, applied_seq)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, row.get("seq", 0), row.get("row_no", 0),
                        row.get("doc_no", ""), row.get("line_no", 0),
                        row.get("doc_type") or "", row.get("warehouse") or "",
                        row.get("sku") or "", str(row.get("qty") or ""),
                        row.get("unit") or "",
                        str(row.get("unit_factor") or "1"), str(row.get("qty_kg") or ""),
                        str(row.get("price") or ""), row.get("ts_utc") or "",
                        row.get("direction", ""), row.get("ref_doc", ""),
                        int(row.get("revision") or 1), row.get("status", "valid"),
                        row.get("reason", ""),
                        json.dumps(row.get("flags", []), ensure_ascii=False),
                        json.dumps(row.get("raw", []), ensure_ascii=False),
                        row.get("raw_qty", ""), row.get("raw_unit", ""),
                        row.get("raw_cost", ""), row.get("raw_ts", ""),
                        int(row.get("applied_seq") or 0),
                    )
                    for row in movements
                ],
            )
            conn.executemany(
                """INSERT INTO layers (run_id, layer_id, sku, warehouse, origin, doc_no,
                    line_no, created_ts, unit_cost, qty_in, qty_remaining)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, layer["layer_id"], layer["sku"], layer["warehouse"],
                        layer["origin"], layer.get("doc_no", ""), layer.get("line_no", 0),
                        layer.get("created_ts", ""), str(layer["unit_cost"]),
                        str(layer["qty_in"]), str(layer["qty_remaining"]),
                    )
                    for layer in layers
                ],
            )
            conn.executemany(
                """INSERT INTO consumptions (run_id, seg_id, out_doc, out_line, sku,
                    warehouse, layer_id, qty, unit_cost, amount, kind)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, seg["seg_id"], seg.get("out_doc", ""), seg.get("out_line", 0),
                        seg.get("sku", ""), seg.get("warehouse", ""), seg["layer_id"],
                        str(seg["qty"]), str(seg["unit_cost"]), str(seg["amount"]),
                        seg.get("kind", ""),
                    )
                    for seg in consumptions
                ],
            )
            conn.executemany(
                """INSERT INTO counts (run_id, count_no, revision, sku, warehouse,
                    counted_qty, system_qty, diff_qty, adjust_qty, adjust_value, status, reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item["count_no"], item.get("revision", 1), item["sku"],
                        item["warehouse"], str(item["counted_qty"]), str(item["system_qty"]),
                        str(item["diff_qty"]), str(item["adjust_qty"]),
                        str(item["adjust_value"]), item.get("status", ""),
                        item.get("reason", ""),
                    )
                    for item in counts
                ],
            )
            conn.executemany(
                "INSERT INTO run_log (run_id, seq, at_utc, level, stage, message)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (run_id, item["seq"], item["at_utc"], item["level"],
                     item["stage"], item["message"])
                    for item in log
                ],
            )

    def replace_counts(self, run_id: str, counts: list[dict]) -> None:
        """盘点记账：整批替换本批的盘点记录（版本闸门已在服务层校验）。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM counts WHERE run_id = ?", (run_id,))
            conn.executemany(
                """INSERT INTO counts (run_id, count_no, revision, sku, warehouse,
                    counted_qty, system_qty, diff_qty, adjust_qty, adjust_value,
                    status, reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item["count_no"], item.get("revision", 1), item["sku"],
                        item["warehouse"], str(item["counted_qty"]), str(item["system_qty"]),
                        str(item["diff_qty"]), str(item["adjust_qty"]),
                        str(item["adjust_value"]), item.get("status", ""),
                        item.get("reason", ""),
                    )
                    for item in counts
                ],
            )

    def set_status(self, run_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))

    def update_totals(self, run_id: str, totals: dict) -> None:
        keys = ("opening_qty", "in_qty", "out_qty", "adj_in_qty", "adj_out_qty",
                "ending_qty", "cogs", "ending_value")
        assignments = ", ".join(f"{key} = ?" for key in keys if key in totals)
        values = [str(totals[key]) for key in keys if key in totals]
        if not assignments:
            return
        with self._conn() as conn:
            conn.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", (*values, run_id)
            )

    # ---------- 行 ----------
    def movements(self, run_id: str, status: str | None = None,
                  reason: str | None = None) -> list[dict]:
        sql = "SELECT * FROM movements WHERE run_id = ?"
        args: list = [run_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        if reason:
            sql += " AND reason = ?"
            args.append(reason)
        sql += " ORDER BY seq"
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["flags"] = json.loads(item.pop("flags_json") or "[]")
            item["raw"] = json.loads(item.pop("raw_json") or "[]")
            out.append(item)
        return out

    def quarantine(self, run_id: str) -> list[dict]:
        return self.movements(run_id, status="quarantine")

    def layers(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM layers WHERE run_id = ? ORDER BY layer_id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def consumptions(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM consumptions WHERE run_id = ? ORDER BY seg_id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM counts WHERE run_id = ? ORDER BY count_no, sku", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def log_events(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM run_log WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_replay(self, run_id: str, layers: list[dict],
                       consumptions: list[dict], movements: list[dict]) -> None:
        """M4 单独重跑：只替换本批的层、消耗段与行状态。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM layers WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM consumptions WHERE run_id = ?", (run_id,))
            conn.executemany(
                """INSERT INTO layers (run_id, layer_id, sku, warehouse, origin, doc_no,
                    line_no, created_ts, unit_cost, qty_in, qty_remaining)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, layer["layer_id"], layer["sku"], layer["warehouse"],
                        layer["origin"], layer.get("doc_no", ""), layer.get("line_no", 0),
                        layer.get("created_ts", ""), str(layer["unit_cost"]),
                        str(layer["qty_in"]), str(layer["qty_remaining"]),
                    )
                    for layer in layers
                ],
            )
            conn.executemany(
                """INSERT INTO consumptions (run_id, seg_id, out_doc, out_line, sku,
                    warehouse, layer_id, qty, unit_cost, amount, kind)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, seg["seg_id"], seg.get("out_doc", ""), seg.get("out_line", 0),
                        seg.get("sku", ""), seg.get("warehouse", ""), seg["layer_id"],
                        str(seg["qty"]), str(seg["unit_cost"]), str(seg["amount"]),
                        seg.get("kind", ""),
                    )
                    for seg in consumptions
                ],
            )
            for row in movements:
                conn.execute(
                    "UPDATE movements SET status = ?, reason = ?, flags_json = ?,"
                    " applied_seq = ? WHERE run_id = ? AND seq = ?",
                    (
                        row.get("status", "valid"), row.get("reason", ""),
                        json.dumps(row.get("flags", []), ensure_ascii=False),
                        int(row.get("applied_seq") or 0), run_id, row.get("seq", 0),
                    ),
                )

    # ---------- 盘点版本水位 ----------
    def count_state(self, count_no: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM count_state WHERE count_no = ?", (count_no,)
            ).fetchone()
        return dict(row) if row else None

    def save_count_state(self, count_no: str, revision: int, payload_sha: str,
                         run_id: str, updated_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO count_state
                (count_no, revision, payload_sha, run_id, updated_at)
                VALUES (?,?,?,?,?)""",
                (count_no, revision, payload_sha, run_id, updated_at),
            )
