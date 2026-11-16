"""导出编排：按 kind 取本批数据，返回文件名与正文。"""
from __future__ import annotations

from ...errors import DomainError
from ..m5_cost import cogs
from ..m6_reconcile import balance
from . import csv_out, manifest_out


def build(store, run, settings, kind: str) -> tuple[str, str, str]:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 409)
    run_id = run["run_id"]
    movements = store.movements(run_id)
    layers = store.layers(run_id)
    segments = store.consumptions(run_id)

    if kind == "movements.csv":
        return (f"movements-{run_id}.csv",
                csv_out.movements_csv(movements), "text/csv; charset=utf-8")
    if kind == "layers.csv":
        return (f"layers-{run_id}.csv",
                csv_out.layers_csv(layers), "text/csv; charset=utf-8")
    if kind == "cost.csv":
        return (f"cost-{run_id}.csv",
                csv_out.cost_csv(cogs.by_doc(segments)), "text/csv; charset=utf-8")
    if kind == "manifest.json":
        report = balance.reconcile(run, movements, layers, segments,
                                   store.counts(run_id))
        return (f"manifest-{run_id}.json",
                manifest_out.manifest_json(run, report["checks"]),
                "application/json; charset=utf-8")
    raise DomainError("E_EXPORT_KIND", f"不支持的导出类型：{kind}", 400)


def summary(store, run) -> dict:
    if not run:
        return {"run_id": None, "files": [], "counts": {}, "totals": {}}
    run_id = run["run_id"]
    segments = store.consumptions(run_id)
    return {
        "run_id": run_id,
        "files": [
            {"kind": "movements.csv", "label": "流水证据行", "rows": run["raw_rows"]},
            {"kind": "layers.csv", "label": "FIFO 层",
             "rows": len(store.layers(run_id))},
            {"kind": "cost.csv", "label": "单据成本", "rows": len(cogs.by_doc(segments))},
            {"kind": "manifest.json", "label": "运行清单", "rows": 1},
        ],
        "counts": {
            "applied": run["applied"],
            "quarantined": run["quarantined"],
            "superseded": run["superseded"],
            "duplicate": run["duplicate"],
        },
        "totals": {
            "opening_qty": run["opening_qty"],
            "in_qty": run["in_qty"],
            "out_qty": run["out_qty"],
            "adj_in_qty": run["adj_in_qty"],
            "adj_out_qty": run["adj_out_qty"],
            "ending_qty": run["ending_qty"],
            "cogs": run["cogs"],
            "ending_value": run["ending_value"],
        },
    }
