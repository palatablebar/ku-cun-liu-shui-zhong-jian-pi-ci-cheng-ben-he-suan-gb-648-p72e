"""盘点：差异计算、调整层生成、版本闸门。

- 系统数量取重放后的层合计（不是调整后的中间值）；
- 差异为正按最新层成本建调整层，差异为负按 FIFO 消耗，消耗不足整行拒绝；
- 同一盘点单同一物料同一仓库只保留最高版本，其次取文件里先出现的一行；
- 版本闸门：新版本才能覆盖，同版本同内容视为幂等重放，同版本不同内容拒绝。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from ... import config
from ..m4_replay import fifo


def canonical(rows: list[dict]) -> str:
    payload = json.dumps(
        sorted(
            [
                [row["count_no"], int(row["revision"]), row["sku"], row["warehouse"],
                 format(config.dec(row["counted_qty"]), "f")]
                for row in rows
            ]
        ),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dedupe(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("codes"):
            continue
        key = (row["count_no"], row["sku"], row["warehouse"])
        groups.setdefault(key, []).append(row)
    kept: list[dict] = []
    for items in groups.values():
        max_revision = max(int(item["revision"]) for item in items)
        contenders = [item for item in items if int(item["revision"]) == max_revision]
        kept.append(contenders[0])
    return sorted(kept, key=lambda item: (item["count_no"], item["sku"], item["warehouse"]))


def system_quantities(layers: list[dict]) -> dict:
    buckets: dict[tuple, Decimal] = {}
    for layer in layers:
        key = (layer["sku"], layer["warehouse"])
        buckets[key] = buckets.get(key, Decimal("0")) + config.dec(layer["qty_remaining"])
    return buckets


def reference_cost(layers: list[dict], sku: str, warehouse: str) -> Decimal | None:
    candidates = [
        layer for layer in layers
        if layer["sku"] == sku and layer["warehouse"] == warehouse
        and config.dec(layer["qty_remaining"]) > 0
    ]
    if not candidates:
        return None
    return config.dec(candidates[-1]["unit_cost"])


def apply(rows: list[dict], layers: list[dict], settings) -> dict:
    working = [
        {**layer, "qty_remaining": config.dec(layer["qty_remaining"])}
        for layer in layers
    ]
    buckets: dict[tuple, list[dict]] = {}
    for layer in working:
        buckets.setdefault((layer["sku"], layer["warehouse"]), []).append(layer)
    systems = system_quantities(working)
    records: list[dict] = []
    adjust_in = Decimal("0")
    adjust_out = Decimal("0")
    adjust_value = Decimal("0")
    seg_counter = [1]

    for row in dedupe(rows):
        key = (row["sku"], row["warehouse"])
        system = systems.get(key, Decimal("0"))
        counted = config.dec(row["counted_qty"])
        diff = config.quantize_qty(counted - system)
        adjust_qty = Decimal("0")
        value = Decimal("0")
        status = "COUNT_BALANCED"
        reason = "COUNT_BALANCED"
        if diff > 0:
            cost = reference_cost(working, row["sku"], row["warehouse"])
            if cost is None:
                cost = Decimal("0")
            status = "COUNT_DIFF"
            reason = "COUNT_DIFF"
            layer = fifo.new_layer(
                working, row["sku"], row["warehouse"], "盘点", row["count_no"], 0,
                "", cost, diff,
            )
            buckets.setdefault(key, []).append(layer)
            adjust_qty = diff
            value = diff * cost
            adjust_in += diff
        elif diff < 0:
            need = -diff
            produced = fifo.consume(
                buckets.setdefault(key, []), need, "C", seg_counter)
            if produced is None:
                status = "COUNT_SHORT"
                reason = "ADJUST_EXCEEDS_STOCK"
            else:
                status = "COUNT_DIFF"
                reason = "COUNT_DIFF"
                adjust_qty = diff
                adjust_out += need
                value = -sum((seg["amount"] for seg in produced), Decimal("0"))
        records.append(
            {
                "count_no": row["count_no"],
                "revision": int(row["revision"]),
                "sku": row["sku"],
                "warehouse": row["warehouse"],
                "counted_qty": counted,
                "system_qty": system,
                "diff_qty": diff,
                "adjust_qty": adjust_qty,
                "adjust_value": value,
                "status": status,
                "reason": reason,
            }
        )
        adjust_value += value

    adjusted_qty = sum((config.dec(layer["qty_remaining"]) for layer in working),
                       Decimal("0"))
    return {
        "layers": working,
        "records": records,
        "totals": {
            "system_qty": sum(systems.values(), Decimal("0")),
            "adjust_in_qty": adjust_in,
            "adjust_out_qty": adjust_out,
            "adjust_qty": adjust_in - adjust_out,
            "adjust_value": adjust_value,
            "adjusted_qty": adjusted_qty,
            "rows": len(records),
        },
    }
