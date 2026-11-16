"""成本视图装配：层、按单据出库成本、按类型成本与三条金额/数量恒等式。"""
from __future__ import annotations

from ... import config
from . import cogs, layers as layers_mod


def payload(store, run) -> dict:
    if not run:
        return {"run_id": None, "layers": [], "layer_rows": [], "layer_totals": {},
                "cogs": [], "cogs_totals": {}, "kinds": [], "checks": []}
    raw_layers = store.layers(run["run_id"])
    segments = store.consumptions(run["run_id"])
    layer_totals = layers_mod.totals(raw_layers)
    cogs_totals = cogs.totals(segments)
    checks = [
        {
            "code": "layer_value",
            "label": "层金额 vs 期末金额",
            "expect": _q(run["ending_value"]),
            "actual": layer_totals["value"],
            "ok": _same(run["ending_value"], layer_totals["value"]),
        },
        {
            "code": "layer_qty",
            "label": "层数量 vs 期末数量",
            "expect": _qq(run["ending_qty"]),
            "actual": layer_totals["qty"],
            "ok": _same(run["ending_qty"], layer_totals["qty"]),
        },
        {
            "code": "cogs",
            "label": "出库成本 vs 消耗段",
            "expect": _q(run["cogs"]),
            "actual": cogs_totals["net"],
            "ok": _same(run["cogs"], cogs_totals["net"]),
        },
    ]
    return {
        "run_id": run["run_id"],
        "layers": raw_layers,
        "layer_rows": layers_mod.summarize(raw_layers),
        "layer_totals": layer_totals,
        "cogs": cogs.by_doc(segments),
        "cogs_totals": cogs_totals,
        "kinds": cogs.by_kind(segments),
        "checks": checks,
    }


def _q(value) -> str:
    return format(config.quantize_value(config.dec(value)), "f")


def _qq(value) -> str:
    return format(config.quantize_qty(config.dec(value)), "f")


def _same(left, right) -> bool:
    return config.quantize_value(config.dec(left)) == config.quantize_value(
        config.dec(right))
