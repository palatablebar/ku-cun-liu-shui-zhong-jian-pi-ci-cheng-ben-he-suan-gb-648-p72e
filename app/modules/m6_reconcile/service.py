"""盘点编排：读实盘文件、算差异、版本闸门、装配对账视图。"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons
from ...errors import DomainError
from ...manifest import sha256_hex, utc_now_iso
from ..m2_doc_rules import coerce
from ..m2_doc_rules import service as m2_service
from . import balance, counts as counts_mod


def parsed_rows(settings, filename: str, raw: bytes) -> dict:
    return m2_service.read_counts(settings, filename, raw)


def build_records(rows: list[dict], layers: list[dict], settings) -> dict:
    """把实盘行落成盘点记录：先按版本去重，再算差异与调整。"""
    valid = [row for row in rows if not row["codes"]]
    invalid = [
        {
            "count_no": row["count_no"] or "-",
            "revision": int(row["revision"]),
            "sku": row["sku"],
            "warehouse": row["warehouse"],
            "counted_qty": config.dec(row["qty_kg"]),
            "system_qty": Decimal("0"),
            "diff_qty": Decimal("0"),
            "adjust_qty": Decimal("0"),
            "adjust_value": Decimal("0"),
            "status": "COUNT_INVALID",
            "reason": reasons.primary(row["codes"]) or "",
        }
        for row in rows
        if row["codes"]
    ]
    outcome = counts_mod.apply(valid, layers, settings)
    outcome["records"] = outcome["records"] + invalid
    return outcome


def import_counts(settings, run_id: str, filename: str, raw: bytes,
                  layers: list[dict]) -> dict:
    parsed = parsed_rows(settings, filename, raw)
    outcome = build_records(parsed["rows"], layers, settings)
    outcome["file"] = filename
    outcome["sha256"] = sha256_hex(raw)
    outcome["bytes"] = len(raw)
    outcome["state"] = parsed["state"]
    outcome["run_id"] = run_id
    return outcome


def payload(store, run) -> dict:
    if not run:
        return {"run_id": None, "checks": [], "totals": {}, "counts": [],
                "ok": False, "rows": {}}
    layers = store.layers(run["run_id"])
    stored = store.counts(run["run_id"])
    input_rows = [
        {
            "count_no": item["count_no"],
            "revision": int(item["revision"]),
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": config.dec(item["counted_qty"]),
            "codes": [],
        }
        for item in stored
        if item.get("status") != "COUNT_INVALID"
    ]
    outcome = counts_mod.apply(input_rows, layers, None)
    invalid = [item for item in stored if item.get("status") == "COUNT_INVALID"]
    records = outcome["records"] + [
        {
            "count_no": item["count_no"],
            "revision": int(item["revision"]),
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": config.dec(item["counted_qty"]),
            "system_qty": config.dec(item["system_qty"]),
            "diff_qty": config.dec(item["diff_qty"]),
            "adjust_qty": config.dec(item["adjust_qty"]),
            "adjust_value": config.dec(item["adjust_value"]),
            "status": item["status"],
            "reason": item["reason"],
        }
        for item in invalid
    ]
    report = balance.reconcile(
        run, store.movements(run["run_id"]), layers, store.consumptions(run["run_id"]),
        records, outcome["totals"]["adjusted_qty"],
    )
    report["counts"] = [
        {
            "count_no": item["count_no"],
            "revision": item["revision"],
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": format(config.quantize_qty(item["counted_qty"]), "f"),
            "system_qty": format(config.quantize_qty(item["system_qty"]), "f"),
            "diff_qty": format(config.quantize_qty(item["diff_qty"]), "f"),
            "adjust_qty": format(config.quantize_qty(item["adjust_qty"]), "f"),
            "adjust_value": format(config.quantize_value(item["adjust_value"]), "f"),
            "status": item["status"],
            "reason": item["reason"],
        }
        for item in records
    ]
    report["adjust"] = {
        "adjust_in_qty": format(
            config.quantize_qty(outcome["totals"]["adjust_in_qty"]), "f"),
        "adjust_out_qty": format(
            config.quantize_qty(outcome["totals"]["adjust_out_qty"]), "f"),
        "adjust_value": format(
            config.quantize_value(outcome["totals"]["adjust_value"]), "f"),
        "adjusted_qty": format(
            config.quantize_qty(outcome["totals"]["adjusted_qty"]), "f"),
    }
    return report


def submit(settings, store, run, body: dict) -> dict:
    """手工提交盘点：校验版本闸门后写入，同版本同内容幂等。"""
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 409)
    if not isinstance(body, dict):
        raise DomainError("E_BAD_BODY", "请求体必须是 JSON 对象", 400)
    count_no = str(body.get("count_no") or "").strip()
    if not count_no:
        raise DomainError("E_COUNT_NO", "缺少盘点单号", 400)
    try:
        revision = int(body.get("revision"))
    except (TypeError, ValueError):
        raise DomainError("E_REVISION", "版本号必须是整数", 400)
    if revision < 1:
        raise DomainError("E_REVISION", "版本号必须 ≥ 1", 400)
    raw_rows = body.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise DomainError("E_COUNT_ROWS", "盘点行不能为空", 400)

    keys = set()
    rows: list[dict] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            raise DomainError("E_COUNT_ROW", "盘点行必须是对象", 400)
        sku = str(item.get("sku") or "").strip()
        warehouse = str(item.get("warehouse") or "").strip()
        if sku not in settings.skus:
            raise DomainError("E_SKU", f"物料未登记：{sku}", 400)
        if warehouse not in settings.warehouses:
            raise DomainError("E_WAREHOUSE", f"仓库未登记：{warehouse}", 400)
        try:
            counted = config.dec(str(item.get("counted_qty")))
        except Exception:
            raise DomainError("E_QTY", "实盘数量无法解析", 400)
        if counted < 0:
            raise DomainError("E_QTY", "实盘数量不能为负", 400)
        unit, factor, code = coerce.normalize_unit(
            item.get("unit") or "kg", settings.unit_factors)
        if code:
            raise DomainError("E_UNIT", "单位不支持", 400)
        key = (sku, warehouse)
        if key in keys:
            raise DomainError("E_COUNT_DUP", "同一物料同一仓库只能一行", 400)
        keys.add(key)
        rows.append(
            {
                "count_no": count_no,
                "revision": revision,
                "sku": sku,
                "warehouse": warehouse,
                "counted_qty": coerce.to_kg(counted, factor),
                "codes": [],
            }
        )

    digest = counts_mod.canonical(rows)
    state = store.count_state(count_no)
    if state:
        if revision < int(state["revision"]):
            raise DomainError(
                "E_STALE_COUNT_REVISION",
                f"版本 {revision} 旧于已记录版本 {state['revision']}，拒绝覆盖", 409,
            )
        if revision == int(state["revision"]) and digest != state["payload_sha"]:
            raise DomainError(
                "E_COUNT_REVISION_CONFLICT",
                f"版本 {revision} 内容与已记录不同，升版本后再提交", 409,
            )

    layers = store.layers(run["run_id"])
    existing = [
        {
            "count_no": item["count_no"],
            "revision": int(item["revision"]),
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": config.dec(item["counted_qty"]),
            "codes": [],
        }
        for item in store.counts(run["run_id"])
        if item["count_no"] != count_no and item.get("status") != "COUNT_INVALID"
    ]
    outcome = counts_mod.apply(existing + rows, layers, None)
    invalid = [
        {
            "count_no": item["count_no"],
            "revision": int(item["revision"]),
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": config.dec(item["counted_qty"]),
            "system_qty": config.dec(item["system_qty"]),
            "diff_qty": config.dec(item["diff_qty"]),
            "adjust_qty": config.dec(item["adjust_qty"]),
            "adjust_value": config.dec(item["adjust_value"]),
            "status": item["status"],
            "reason": item["reason"],
        }
        for item in store.counts(run["run_id"])
        if item.get("status") == "COUNT_INVALID"
    ]
    store.replace_counts(run["run_id"], outcome["records"] + invalid)
    store.save_count_state(count_no, revision, digest, run["run_id"], utc_now_iso())
    return payload(store, run)

