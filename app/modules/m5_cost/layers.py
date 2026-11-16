"""层视图：按物料 + 仓库汇总在手、层金额与层数。

每个层金额 = 剩余数量 × 层成本（精确乘积），对外展示再量化到四位。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m4_replay import fifo


def summarize(layers: list[dict]) -> list[dict]:
    buckets: dict[tuple, dict] = {}
    for layer in layers:
        key = (layer["sku"], layer["warehouse"])
        item = buckets.setdefault(
            key,
            {
                "sku": layer["sku"],
                "warehouse": layer["warehouse"],
                "qty": Decimal("0"),
                "value": Decimal("0"),
                "layers": 0,
                "origins": [],
            },
        )
        item["qty"] += config.dec(layer["qty_remaining"])
        item["value"] += fifo.layer_value(layer)
        item["layers"] += 1
        if layer["origin"] not in item["origins"]:
            item["origins"].append(layer["origin"])
    out = []
    for item in sorted(buckets.values(), key=lambda row: (row["sku"], row["warehouse"])):
        out.append(
            {
                "sku": item["sku"],
                "warehouse": item["warehouse"],
                "qty": format(config.quantize_qty(item["qty"]), "f"),
                "value": format(config.quantize_value(item["value"]), "f"),
                "unit_cost": format(
                    (item["value"] / item["qty"]).quantize(config.COST_QUANTUM), "f"
                ) if item["qty"] else "0.0000",
                "layers": item["layers"],
                "origins": item["origins"],
            }
        )
    return out


def totals(layers: list[dict]) -> dict:
    qty = sum((config.dec(layer["qty_remaining"]) for layer in layers), Decimal("0"))
    value = sum((fifo.layer_value(layer) for layer in layers), Decimal("0"))
    return {
        "qty": format(config.quantize_qty(qty), "f"),
        "value": format(config.quantize_value(value), "f"),
        "layers": len(layers),
        "empty": sum(1 for layer in layers
                     if config.dec(layer["qty_remaining"]) == 0),
    }
