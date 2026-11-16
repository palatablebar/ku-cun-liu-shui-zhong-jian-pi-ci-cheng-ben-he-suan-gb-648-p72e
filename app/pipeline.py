"""平台编排：采信 → 质量 → 重放 → 成本 → 盘点对账。

模块之间只通过本文件衔接，模块内部不互相 import 规则函数；同一组输入得到同一 run_id，
重跑先删后插，行数、合计与首跑一致。
"""
from __future__ import annotations

import time
from decimal import Decimal

from . import reasons
from .config import VALUE_QUANTUM, Settings
from .manifest import make_run_id, utc_now_iso
from .modules.m1_ingest import service as m1_service
from .modules.m2_doc_rules import contract
from .modules.m2_doc_rules import service as m2_service
from .modules.m3_quality import service as m3_service
from .modules.m4_replay import service as m4_service
from .modules.m6_reconcile import balance as balance_mod
from .modules.m6_reconcile import service as m6_service

DEFAULT_MOVEMENTS = "movements_dirty.csv"
DEFAULT_OPENING = "opening_balance.csv"
DEFAULT_COUNTS = "physical_counts.csv"

TRANSFER_REASONS = (
    "TRN_MISSING_LEG",
    "TRN_QTY_MISMATCH",
    "TRN_SKU_MISMATCH",
    "TRN_SAME_WAREHOUSE",
    "TRN_LEG_AMBIGUOUS",
    "TRN_LEG_BLOCKED",
)


def _fmt(value) -> str:
    """日志里只显示四位小数，避免八位尾数刷屏。"""
    return format(Decimal(value).quantize(VALUE_QUANTUM), "f")


def _load(settings: Settings, filename: str, raw: bytes | None, aliases: dict):
    """解析一份输入；raw 为空时从样例目录按文件名读取。返回 (解析结果, 原始字节)。"""
    if raw is None:
        filename, raw = m1_service.resolve_sample(settings, filename)
    parsed = m1_service.read_file(settings, filename, raw, aliases)
    return parsed, raw


def run_chain(settings: Settings, store, movements=None, opening=None,
              counts=None) -> dict:
    """跑完整主流程；三份输入可分别给 (文件名, 字节) 或留空用样例。"""
    started = time.monotonic()
    log: list[dict] = []

    def note(level: str, stage: str, message: str) -> None:
        log.append(
            {
                "seq": len(log) + 1,
                "at_utc": utc_now_iso(),
                "level": level,
                "stage": stage,
                "message": message,
            }
        )

    mov_name, mov_raw = movements if movements else (DEFAULT_MOVEMENTS, None)
    open_name, open_raw = opening if opening else (DEFAULT_OPENING, None)
    count_name, count_raw = counts if counts else (DEFAULT_COUNTS, None)

    parsed_movements, movements_bytes = _load(
        settings, mov_name, mov_raw, contract.aliases("movements"))
    note(
        "INFO", "采信",
        f"{parsed_movements['filename']} 编码 {parsed_movements['encoding']}"
        f" 段 {parsed_movements['segments']} 数据行 {parsed_movements['raw_rows']}"
        f" 空行 {parsed_movements['blank_rows']}"
        f" 状态 {parsed_movements['state'] or 'OK'}",
    )
    if parsed_movements["state"]:
        note("WARN", "采信", f"输入状态 {parsed_movements['state']}")

    built = m3_service.build(settings, parsed_movements)
    rows = built["rows"]
    note(
        "INFO", "质量",
        f"采纳 {sum(1 for row in rows if row['status'] == 'valid')}"
        f" 隔离 {sum(1 for row in rows if row['status'] == 'quarantine')}"
        f" 被取代 {sum(1 for row in rows if row['status'] == 'superseded')}"
        f" 重复 {sum(1 for row in rows if row['status'] == 'duplicate')}",
    )

    parsed_opening, opening_bytes = _load(
        settings, open_name, open_raw, contract.aliases("opening"))
    opening_rows = m2_service.read_opening(
        settings, parsed_opening["filename"], opening_bytes)["rows"]
    note(
        "INFO", "采信",
        f"{parsed_opening['filename']} 期初行 {len(opening_rows)}"
        f" 有效 {sum(1 for row in opening_rows if row['status'] == 'valid')}",
    )

    outcome = m4_service.replay_rows(settings, rows, opening_rows)
    totals = outcome["totals"]
    note(
        "INFO", "重放",
        f"调拨对 {outcome['pairs']} 层 {len(outcome['layers'])}"
        f" 消耗段 {len(outcome['segments'])}",
    )
    note(
        "WARN" if any(row["reason"] == "OUT_EXCEEDS_STOCK" for row in rows) else "INFO",
        "重放",
        f"出库超在手 {sum(1 for row in rows if row['reason'] == 'OUT_EXCEEDS_STOCK')}"
        f" 调拨问题 {sum(1 for row in rows if row['reason'] in TRANSFER_REASONS)}",
    )

    parsed_counts, counts_bytes = _load(
        settings, count_name, count_raw, contract.aliases("counts"))
    count_input = m2_service.read_counts(
        settings, parsed_counts["filename"], counts_bytes)
    count_outcome = m6_service.build_records(
        count_input["rows"], outcome["layers"], settings)
    count_records = count_outcome["records"]
    note(
        "INFO", "对账",
        f"{parsed_counts['filename']} 盘点行 {len(count_records)}"
        f" 差异 {sum(1 for item in count_records if item['status'] == 'COUNT_DIFF')}"
        f" 拒绝 {sum(1 for item in count_records if item['status'] == 'COUNT_SHORT')}",
    )

    applied = sum(1 for row in rows if row["status"] == "valid")
    params = settings.params()
    run = {
        "run_id": make_run_id(
            parsed_movements["sha256"], parsed_opening["sha256"],
            parsed_counts["sha256"], settings.rule_version, params),
        "created_at": utc_now_iso(),
        "rule_version": settings.rule_version,
        "params": params,
        "movements_sha256": parsed_movements["sha256"],
        "opening_sha256": parsed_opening["sha256"],
        "counts_sha256": parsed_counts["sha256"],
        "movements_file": parsed_movements["filename"],
        "opening_file": parsed_opening["filename"],
        "counts_file": parsed_counts["filename"],
        "movements_bytes": len(movements_bytes),
        "opening_bytes": len(opening_bytes),
        "counts_bytes": len(counts_bytes),
        "raw_rows": len(rows),
        "applied": applied,
        "quarantined": sum(1 for row in rows if row["status"] == "quarantine"),
        "superseded": sum(1 for row in rows if row["status"] == "superseded"),
        "duplicate": sum(1 for row in rows if row["status"] == "duplicate"),
        "blank": parsed_movements["blank_rows"],
        "segments": parsed_movements["segments"],
        "opening_qty": totals["opening_qty"],
        "in_qty": totals["in_qty"],
        "out_qty": totals["out_qty"],
        "adj_in_qty": totals["adj_in_qty"],
        "adj_out_qty": totals["adj_out_qty"],
        "ending_qty": totals["ending_qty"],
        "cogs": totals["cogs"],
        "ending_value": totals["ending_value"],
        "status": "ready" if applied else "empty",
        "duration_ms": 0,
    }
    report = balance_mod.reconcile(
        run, rows, outcome["layers"], outcome["segments"], count_records,
        count_outcome["totals"]["adjusted_qty"],
    )
    note(
        "INFO", "对账",
        f"六本账 {'通过' if report['ok'] else '有差异'}"
        f" 期末 {_fmt(totals['ending_qty'])} kg",
    )
    note(
        "INFO", "成本",
        f"出库成本 {_fmt(totals['cogs'])} 期末金额 {_fmt(totals['ending_value'])}",
    )
    run["duration_ms"] = int((time.monotonic() - started) * 1000)
    note("INFO", "完成", f"运行 {run['run_id']} 用时 {run['duration_ms']} ms")

    store.save_run(run, rows, outcome["layers"], outcome["segments"], count_records, log)
    return summary(run, rows, outcome, count_records, report)


def summary(run: dict, rows: list[dict], outcome: dict,
            count_records: list[dict], report: dict) -> dict:
    totals = outcome["totals"]
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "created_at": run["created_at"],
        "counts": {
            "raw_rows": len(rows),
            "applied": run["applied"],
            "quarantined": run["quarantined"],
            "superseded": run["superseded"],
            "duplicate": run["duplicate"],
            "blank": run["blank"],
            "segments": run["segments"],
        },
        "quantities": {
            key: format(totals[key], "f")
            for key in ("opening_qty", "in_qty", "out_qty", "adj_in_qty",
                        "adj_out_qty", "ending_qty")
        },
        "cogs": format(totals["cogs"], "f"),
        "ending_value": format(totals["ending_value"], "f"),
        "layers": len(outcome["layers"]),
        "segments": len(outcome["segments"]),
        "counts_rows": len(count_records),
        "reconcile_ok": report["ok"],
        "checks": report["checks"],
        "quarantine": reasons.distribution(
            [row for row in rows if row["status"] == "quarantine"]),
        "movements_sha256": run["movements_sha256"],
        "opening_sha256": run["opening_sha256"],
        "counts_sha256": run["counts_sha256"],
    }


def run_demo(settings: Settings, store) -> dict:
    return run_chain(settings, store)


def import_movements(settings: Settings, store, filename: str, raw: bytes) -> dict:
    previous = store.latest_run() or {}
    return run_chain(
        settings, store,
        movements=(filename, raw),
        opening=(previous.get("opening_file") or DEFAULT_OPENING, None),
        counts=(previous.get("counts_file") or DEFAULT_COUNTS, None),
    )


def import_opening(settings: Settings, store, filename: str, raw: bytes) -> dict:
    previous = store.latest_run() or {}
    return run_chain(
        settings, store,
        movements=(previous.get("movements_file") or DEFAULT_MOVEMENTS, None),
        opening=(filename, raw),
        counts=(previous.get("counts_file") or DEFAULT_COUNTS, None),
    )
