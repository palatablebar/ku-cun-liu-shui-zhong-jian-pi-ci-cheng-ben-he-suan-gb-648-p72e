"""质量编排：归一 → 定级 → 隔离区视图。"""
from __future__ import annotations

from ... import reasons
from ..m2_doc_rules import service as m2_service
from . import quarantine, rules


def build(settings, parsed: dict) -> dict:
    rows: list[dict] = []
    for index, record in enumerate(parsed["records"], 1):
        record = dict(record)
        record["seq"] = index
        rows.append(m2_service.normalize_movement(record, settings))
    outcome = rules.build(rows)
    outcome["state"] = parsed["state"]
    outcome["blank_rows"] = parsed["blank_rows"]
    return outcome


def payload(store, run) -> dict:
    if not run:
        return {"run_id": None, "rows": [], "distribution": [], "count": 0}
    rows = store.quarantine(run["run_id"])
    return {
        "run_id": run["run_id"],
        "rows": rows,
        "distribution": quarantine.distribution(rows),
        "count": len(rows),
        "totals": quarantine.totals(rows),
        "states": reasons.REASONS,
    }


def csv(store, run) -> tuple[str, str]:
    filename = f"quarantine-{(run or {}).get('run_id', 'none')}.csv"
    rows = store.quarantine(run["run_id"]) if run else []
    return filename, quarantine.csv_text(rows)
