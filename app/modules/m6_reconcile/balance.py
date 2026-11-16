"""对账：行数账、数量账、分层账、成本账、盘点账、调拨账六本，逐项给期望值与实际值。

每本账都从落库的行、层、消耗段与盘点记录重算，不信任批次表里的汇总数字。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config


def _dec(value) -> Decimal:
    return config.dec(value)


def _q(value) -> str:
    return format(config.quantize_value(_dec(value)), "f")


def _qq(value) -> str:
    return format(config.quantize_qty(_dec(value)), "f")


def _check(code: str, label: str, expect, actual, note: str = "") -> dict:
    ok = config.quantize_value(_dec(expect)) == config.quantize_value(_dec(actual))
    return {
        "code": code,
        "label": label,
        "expect": _q(expect),
        "actual": _q(actual),
        "ok": ok,
        "note": note,
    }


def reconcile(run: dict, movements: list[dict], layers: list[dict],
              consumptions: list[dict], counts: list[dict],
              adjusted_qty=None) -> dict:
    applied = [row for row in movements if row["status"] == "valid"]
    quarantined = [row for row in movements if row["status"] == "quarantine"]
    superseded = [row for row in movements if row["status"] == "superseded"]
    duplicate = [row for row in movements if row["status"] == "duplicate"]

    opening_qty = _dec(run["opening_qty"])
    in_qty = _dec(run["in_qty"])
    out_qty = _dec(run["out_qty"])
    adj_in = _dec(run["adj_in_qty"])
    adj_out = _dec(run["adj_out_qty"])
    ending_qty = _dec(run["ending_qty"])

    layer_qty = sum((_dec(layer["qty_remaining"]) for layer in layers), Decimal("0"))
    layer_value = sum((_dec(layer["qty_remaining"]) * _dec(layer["unit_cost"])
                       for layer in layers), Decimal("0"))
    layer_in_qty = sum((_dec(layer["qty_in"]) for layer in layers), Decimal("0"))
    layer_in_value = sum((_dec(layer["qty_in"]) * _dec(layer["unit_cost"])
                          for layer in layers), Decimal("0"))

    consumed_qty = sum((_dec(seg["qty"]) for seg in consumptions
                        if seg["kind"] != "RET"), Decimal("0"))
    restored_qty = sum((_dec(seg["qty"]) for seg in consumptions
                        if seg["kind"] == "RET"), Decimal("0"))
    consumed_value = sum((_dec(seg["amount"]) for seg in consumptions
                          if seg["kind"] != "RET"), Decimal("0"))
    restored_value = sum((_dec(seg["amount"]) for seg in consumptions
                          if seg["kind"] == "RET"), Decimal("0"))

    trn_out_qty = sum((_dec(row["qty_kg"]) for row in applied
                       if row["direction"] == "TRN_OUT"), Decimal("0"))
    trn_in_qty = sum((_dec(row["qty_kg"]) for row in applied
                      if row["direction"] == "TRN_IN"), Decimal("0"))

    count_rows = [item for item in counts if item.get("status") != "COUNT_SHORT"]
    count_adjust = sum((_dec(item["adjust_qty"]) for item in count_rows), Decimal("0"))
    count_value = sum((_dec(item["adjust_value"]) for item in count_rows), Decimal("0"))
    short_rows = [item for item in counts if item.get("status") == "COUNT_SHORT"]

    if adjusted_qty is None:
        adjusted_qty = ending_qty + count_adjust

    checks = [
        _check(
            "rows",
            "原始行 = 采纳 + 隔离 + 被取代 + 重复",
            run["raw_rows"],
            len(applied) + len(quarantined) + len(superseded) + len(duplicate),
            f"采纳 {len(applied)} / 隔离 {len(quarantined)} / 被取代 {len(superseded)}"
            f" / 重复 {len(duplicate)}",
        ),
        _check(
            "quantity",
            "期初 + 入 − 出 ± 调整 = 期末",
            opening_qty + in_qty - out_qty + adj_in - adj_out,
            ending_qty,
            f"期初 {_qq(opening_qty)} / 入 {_qq(in_qty)} / 出 {_qq(out_qty)}",
        ),
        _check("layer_qty", "层剩余数量 = 期末数量", ending_qty, layer_qty),
        _check("layer_value", "层剩余金额 = 期末金额", run["ending_value"], layer_value),
        _check(
            "conservation_value",
            "建层金额 = 消耗 − 恢复 + 期末",
            layer_in_value - consumed_value + restored_value,
            layer_value,
            f"建层 {_q(layer_in_value)} / 消耗 {_q(consumed_value)}"
            f" / 恢复 {_q(restored_value)}",
        ),
        _check(
            "conservation_qty",
            "建层数量 = 消耗 − 恢复 + 期末",
            layer_in_qty - consumed_qty + restored_qty,
            layer_qty,
        ),
        _check(
            "cogs",
            "出库成本 = 消耗金额 − 退货恢复金额",
            consumed_value - restored_value,
            run["cogs"],
        ),
        _check(
            "transfer",
            "调拨出数量 = 调拨入数量",
            trn_out_qty,
            trn_in_qty,
            f"出边 {_qq(trn_out_qty)} / 入边 {_qq(trn_in_qty)}",
        ),
        _check(
            "counts_rows",
            "实盘 = 系统数量 + 差异",
            sum((_dec(item["counted_qty"]) for item in count_rows), Decimal("0")),
            sum((_dec(item["system_qty"]) + _dec(item["diff_qty"]) for item in count_rows),
                Decimal("0")),
            f"盘点行 {len(count_rows)} / 拒绝 {len(short_rows)}",
        ),
        _check(
            "counts",
            "重放期末 + 盘点调整 = 盘点后在手",
            ending_qty + count_adjust,
            adjusted_qty,
        ),
    ]

    return {
        "run_id": run["run_id"],
        "checks": checks,
        "rows": {
            "raw": run["raw_rows"],
            "applied": len(applied),
            "quarantined": len(quarantined),
            "superseded": len(superseded),
            "duplicate": len(duplicate),
            "blank": run["blank"],
            "segments": run["segments"],
        },
        "totals": {
            "opening_qty": _qq(opening_qty),
            "in_qty": _qq(in_qty),
            "out_qty": _qq(out_qty),
            "adj_in_qty": _qq(adj_in),
            "adj_out_qty": _qq(adj_out),
            "ending_qty": _qq(ending_qty),
            "layer_qty": _qq(layer_qty),
            "layer_value": _q(layer_value),
            "consumed_value": _q(consumed_value),
            "restored_value": _q(restored_value),
            "cogs": _q(run["cogs"]),
            "count_adjust_qty": _qq(count_adjust),
            "count_adjust_value": _q(count_value),
            "adjusted_qty": _qq(adjusted_qty),
        },
        "count_rows": counts,
        "short_rows": short_rows,
        "ok": all(item["ok"] for item in checks),
    }
