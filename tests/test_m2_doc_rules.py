"""M2 单据规则：千分位/全角、空值哨兵、前导零编码、单位换算、时间归一、单价义务、方向。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m2_doc_rules import coerce
from app.modules.m2_doc_rules import service as m2_service

BASE = {
    "doc_no": "RK-7001",
    "line_no": "1",
    "doc_type": "采购入库",
    "warehouse": "WH-A",
    "sku": "000123",
    "qty": "10",
    "unit": "kg",
    "price": "10.0000",
    "ts": "2026-06-01T09:00:00+08:00",
    "direction": "入",
    "ref_doc": "",
    "revision": "1",
}


def movement(settings, **overrides) -> dict:
    cells = dict(BASE)
    cells.update(overrides)
    return m2_service.normalize_movement(
        {
            "row_no": 2,
            "cells": cells,
            "raw": list(cells.values()),
            "column_ok": True,
            "truncated": False,
        },
        settings,
    )


def test_thousands_fullwidth_and_suffix(settings):
    # TRAP-06：1,200 / １２.５ / ￥1,234.5 / 1200kg
    assert coerce.parse_decimal("1,200")[0] == Decimal("1200")
    assert coerce.parse_decimal("１２.５")[0] == Decimal("12.5")
    assert coerce.parse_decimal("￥1,234.5")[0] == Decimal("1234.5")
    assert coerce.parse_decimal("1200kg")[0] == Decimal("1200")
    assert coerce.parse_decimal("(12.5)")[0] == Decimal("-12.5")
    assert movement(settings, qty="1,200")["qty_kg"] == Decimal("1200.0000")


def test_sentinels_are_not_zero(settings):
    # TRAP-07：空值哨兵当成 0 会把合计拉低
    assert coerce.parse_decimal("-") == (None, "SENTINEL")
    assert coerce.parse_decimal("N/A") == (None, "SENTINEL")
    assert coerce.parse_decimal("  ") == (None, "SENTINEL")
    row = movement(settings, qty="-")
    assert "QTY_MISSING" in row["codes"]
    assert row["qty"] is None and row["qty_kg"] is None


def test_leading_zero_sku_kept_as_string(settings):
    # TRAP-08：int() 化会把 000123 和 123 合并
    assert coerce.parse_sku("'000123") == "000123"
    assert movement(settings, sku="000123")["codes"] == []
    assert movement(settings, sku="123")["codes"] == ["SKU_UNKNOWN"]
    assert movement(settings, sku=" 000456 ")["sku"] == "000456"


def test_unit_conversion_g_to_kg(settings):
    # TRAP-15：g 与 kg 混用，合计会差 1000 倍
    unit, factor, code = coerce.normalize_unit("g", settings.unit_factors)
    assert (unit, factor, code) == ("g", Decimal("0.001"), None)
    assert coerce.normalize_unit("件", settings.unit_factors)[2] == "UNIT_UNKNOWN"
    assert coerce.normalize_unit("", settings.unit_factors)[2] == "UNIT_MISSING"
    row = movement(settings, qty="1,000", unit="g", price="12.0000")
    assert row["qty_kg"] == Decimal("1.0000")
    assert row["qty"] == Decimal("1000")


def test_instant_offsets_collapse_to_utc(settings):
    first = coerce.parse_instant("2026-03-02T09:00:00+08:00")[0]
    second = coerce.parse_instant("2026-03-02T01:00:00Z")[0]
    assert first == second == "2026-03-02T01:00:00+00:00"
    assert coerce.parse_instant("2026/03/02 09:00")[0] == first
    assert coerce.parse_instant("2026-03-02")[0] == "2026-03-01T16:00:00+00:00"
    assert coerce.parse_instant("2026-13-45 99:00") == (None, "TS_PARSE")


def test_price_required_by_doc_type(settings):
    assert "PRICE_REQUIRED" in movement(settings, price="")["codes"]
    assert "PRICE_REQUIRED" not in movement(
        settings, doc_type="退货入库", ref_doc="CK-1", price="")["codes"]
    assert "PRICE_REQUIRED" in movement(
        settings, doc_type="退货入库", ref_doc="", price="")["codes"]
    assert "PRICE_REQUIRED" not in movement(
        settings, doc_type="销售出库", price="")["codes"]
    assert "PRICE_REQUIRED" in movement(
        settings, doc_type="盘点调整", direction="入", price="")["codes"]
    assert "PRICE_REQUIRED" not in movement(
        settings, doc_type="盘点调整", direction="出", price="")["codes"]


def test_direction_resolution(settings):
    assert movement(settings, doc_type="调拨", direction="出")["direction"] == "TRN_OUT"
    assert movement(settings, doc_type="调拨", direction="入")["direction"] == "TRN_IN"
    missing = movement(settings, doc_type="调拨", direction="")
    assert missing["direction"] == ""
    assert "DIRECTION_UNKNOWN" in missing["codes"]
    assert movement(
        settings, doc_type="盘点调整", direction="入", price="10")["direction"] == "ADJ_IN"


def test_contract_endpoint_exposes_registry(client):
    payload = client.get("/api/contract").json()
    assert len(payload["warehouses"]) == 3
    assert len(payload["doc_types"]) == 6
    assert [item["code"] for item in payload["units"]] == ["kg", "g"]
    assert len(payload["samples"]) >= 5
    assert "-" in payload["sentinels"]
    assert payload["quantums"]["qty"] == "0.0001"


def test_normalized_row_keeps_raw_values(settings):
    row = movement(settings, qty="1,200", unit="KG", price="１２.００００")
    assert row["raw_qty"] == "1,200"
    assert row["raw_unit"] == "KG"
    assert row["unit"] == "kg"
    assert row["qty_kg"] == Decimal("1200.0000")
    assert row["price"] == Decimal("12.0000")
    assert row["ts_utc"] == "2026-06-01T01:00:00+00:00"
