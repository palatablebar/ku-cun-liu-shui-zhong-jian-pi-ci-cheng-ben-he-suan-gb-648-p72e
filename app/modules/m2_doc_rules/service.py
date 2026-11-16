"""单据统计与三份文件的字段归一。

- normalize_movement：把采信层给出的原始单元格变成类型化的一行；
- read_opening / read_counts：期初与实盘走同一套归一函数，只是必填列不同。
"""
from __future__ import annotations

from decimal import Decimal

from ...manifest import sha256_hex
from ..m1_ingest import service as m1_service
from . import coerce, contract


def normalize_movement(record: dict, settings) -> dict:
    cells = record["cells"]
    codes: list[str] = []
    if record["truncated"]:
        codes.append("TRUNCATED_TAIL")
    elif not record["column_ok"]:
        codes.append("COLUMN_COUNT")

    doc_no = coerce.normalize_cell(cells.get("doc_no", ""))
    line_no, line_code = coerce.parse_int(cells.get("line_no", ""), settings.sentinels)
    revision, revision_code = coerce.parse_int(cells.get("revision", ""), settings.sentinels)
    doc_type = coerce.normalize_cell(cells.get("doc_type", ""))
    warehouse = coerce.normalize_cell(cells.get("warehouse", ""))
    sku = coerce.parse_sku(cells.get("sku", ""))
    raw_qty = cells.get("qty", "")
    raw_unit = cells.get("unit", "")
    raw_cost = cells.get("price", "")
    raw_ts = cells.get("ts", "")

    if not doc_no:
        codes.append("DOC_NO_MISSING")
    if line_code or line_no is None or line_no < 1:
        codes.append("LINE_NO_INVALID")
    if revision_code or revision is None or revision < 1:
        codes.append("REVISION_INVALID")
        revision = 1
    spec = contract.doc_type_spec(doc_type)
    if spec is None:
        codes.append("DOC_TYPE_UNKNOWN")
    if warehouse not in settings.warehouses:
        codes.append("WAREHOUSE_UNKNOWN")
    if sku not in settings.skus:
        codes.append("SKU_UNKNOWN")

    qty, qty_code = coerce.parse_decimal(raw_qty, settings.sentinels)
    if qty_code == "SENTINEL":
        codes.append("QTY_MISSING")
    elif qty_code == "PARSE":
        codes.append("QTY_PARSE")
    elif qty is not None and qty <= 0:
        codes.append("QTY_NON_POSITIVE")

    unit, factor, unit_code = coerce.normalize_unit(raw_unit, settings.unit_factors)
    if unit_code:
        codes.append(unit_code)

    ts_utc, ts_code = coerce.parse_instant(raw_ts)
    if ts_code:
        codes.append(ts_code)

    direction = contract.direction_for(doc_type, cells.get("direction", ""))
    if spec is not None and direction is None:
        codes.append("DIRECTION_UNKNOWN")

    price, price_code = coerce.parse_decimal(raw_cost, settings.sentinels)
    ref_doc = coerce.normalize_cell(cells.get("ref_doc", ""))
    if contract.price_required(doc_type, direction, ref_doc):
        if price_code == "SENTINEL":
            codes.append("PRICE_REQUIRED")
        elif price_code == "PARSE":
            codes.append("PRICE_PARSE")
    elif price_code == "PARSE":
        codes.append("PRICE_PARSE")

    qty_kg = coerce.to_kg(qty, factor)
    return {
        "seq": record.get("seq", 0),
        "row_no": record["row_no"],
        "raw": record["raw"],
        "doc_no": doc_no,
        "line_no": line_no or 0,
        "revision": revision or 1,
        "doc_type": doc_type,
        "warehouse": warehouse,
        "sku": sku,
        "qty": qty,
        "qty_kg": qty_kg,
        "unit": unit or "",
        "unit_factor": factor,
        "price": price,
        "ts_utc": ts_utc,
        "direction": direction or "",
        "ref_doc": ref_doc,
        "codes": codes,
        "raw_qty": raw_qty,
        "raw_unit": raw_unit,
        "raw_cost": raw_cost,
        "raw_ts": raw_ts,
    }


def read_opening(settings, filename: str, raw: bytes) -> dict:
    parsed = m1_service.read_file(settings, filename, raw, contract.aliases("opening"))
    rows: list[dict] = []
    for index, record in enumerate(parsed["records"], 1):
        cells = record["cells"]
        codes: list[str] = []
        if record["truncated"]:
            codes.append("TRUNCATED_TAIL")
        elif not record["column_ok"]:
            codes.append("COLUMN_COUNT")
        warehouse = coerce.normalize_cell(cells.get("warehouse", ""))
        sku = coerce.parse_sku(cells.get("sku", ""))
        qty, qty_code = coerce.parse_decimal(cells.get("qty", ""), settings.sentinels)
        unit, factor, unit_code = coerce.normalize_unit(
            cells.get("unit", ""), settings.unit_factors)
        price, price_code = coerce.parse_decimal(cells.get("price", ""), settings.sentinels)
        if warehouse not in settings.warehouses:
            codes.append("WAREHOUSE_UNKNOWN")
        if sku not in settings.skus:
            codes.append("SKU_UNKNOWN")
        if qty_code == "SENTINEL":
            codes.append("QTY_MISSING")
        elif qty_code == "PARSE":
            codes.append("QTY_PARSE")
        elif qty is not None and qty < 0:
            codes.append("QTY_NON_POSITIVE")
        if unit_code:
            codes.append(unit_code)
        if price_code == "SENTINEL":
            codes.append("PRICE_REQUIRED")
        elif price_code == "PARSE":
            codes.append("PRICE_PARSE")
        elif price is not None and price < 0:
            codes.append("PRICE_PARSE")
        rows.append(
            {
                "seq": index,
                "row_no": record["row_no"],
                "warehouse": warehouse,
                "sku": sku,
                "qty": qty,
                "qty_kg": coerce.to_kg(qty, factor),
                "unit": unit or "",
                "price": price,
                "codes": codes,
                "status": "quarantine" if codes else "valid",
                "reason": codes[0] if codes else "",
                "flags": ["ZERO_OPENING"] if (not codes and qty == 0) else [],
            }
        )
    parsed["rows"] = rows
    parsed["valid"] = [row for row in rows if row["status"] == "valid"]
    parsed["quarantined"] = [row for row in rows if row["status"] == "quarantine"]
    return parsed


def read_counts(settings, filename: str, raw: bytes) -> dict:
    parsed = m1_service.read_file(settings, filename, raw, contract.aliases("counts"))
    rows: list[dict] = []
    for index, record in enumerate(parsed["records"], 1):
        cells = record["cells"]
        codes: list[str] = []
        if record["truncated"]:
            codes.append("TRUNCATED_TAIL")
        elif not record["column_ok"]:
            codes.append("COLUMN_COUNT")
        count_no = coerce.normalize_cell(cells.get("count_no", ""))
        revision, revision_code = coerce.parse_int(cells.get("revision", ""), settings.sentinels)
        warehouse = coerce.normalize_cell(cells.get("warehouse", ""))
        sku = coerce.parse_sku(cells.get("sku", ""))
        qty, qty_code = coerce.parse_decimal(cells.get("qty", ""), settings.sentinels)
        unit, factor, unit_code = coerce.normalize_unit(
            cells.get("unit", ""), settings.unit_factors)
        if not count_no:
            codes.append("DOC_NO_MISSING")
        if revision_code or revision is None or revision < 1:
            codes.append("REVISION_INVALID")
        if warehouse not in settings.warehouses:
            codes.append("WAREHOUSE_UNKNOWN")
        if sku not in settings.skus:
            codes.append("SKU_UNKNOWN")
        if qty_code == "SENTINEL":
            codes.append("QTY_MISSING")
        elif qty_code == "PARSE":
            codes.append("QTY_PARSE")
        elif qty is not None and qty < 0:
            codes.append("QTY_NON_POSITIVE")
        if unit_code:
            codes.append(unit_code)
        rows.append(
            {
                "seq": index,
                "row_no": record["row_no"],
                "count_no": count_no,
                "revision": revision or 1,
                "warehouse": warehouse,
                "sku": sku,
                "counted_qty": qty,
                "qty_kg": coerce.to_kg(qty, factor),
                "unit": unit or "",
                "codes": codes,
            }
        )
    parsed["rows"] = rows
    return parsed


def payload(settings) -> dict:
    return contract.describe(settings)


def sha(raw: bytes) -> str:
    return sha256_hex(raw)


def zero() -> Decimal:
    return Decimal("0")
