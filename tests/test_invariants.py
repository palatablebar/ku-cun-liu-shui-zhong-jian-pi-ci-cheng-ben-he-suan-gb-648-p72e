"""不变量：层守恒、层边界、调拨金额对称、盘点调整守恒、重跑稳定、原因码优先顺序。"""
from __future__ import annotations

import itertools
from decimal import Decimal

from app import reasons
from app.modules.m6_reconcile import counts as counts_mod


def test_layer_conservation(client, demo):
    store = client.app.state.store
    run_id = demo["run_id"]
    layers = store.layers(run_id)
    segments = store.consumptions(run_id)
    qty_in = sum((Decimal(layer["qty_in"]) for layer in layers), Decimal("0"))
    remain = sum((Decimal(layer["qty_remaining"]) for layer in layers), Decimal("0"))
    consumed = sum((Decimal(seg["qty"]) for seg in segments if seg["kind"] != "RET"),
                   Decimal("0"))
    restored = sum((Decimal(seg["qty"]) for seg in segments if seg["kind"] == "RET"),
                   Decimal("0"))
    assert qty_in - consumed + restored == remain == Decimal(demo["quantities"]["ending_qty"])
    value_in = sum((Decimal(layer["qty_in"]) * Decimal(layer["unit_cost"])
                    for layer in layers), Decimal("0"))
    value_remain = sum((Decimal(layer["qty_remaining"]) * Decimal(layer["unit_cost"])
                        for layer in layers), Decimal("0"))
    value_consumed = sum((Decimal(seg["amount"]) for seg in segments
                          if seg["kind"] != "RET"), Decimal("0"))
    value_restored = sum((Decimal(seg["amount"]) for seg in segments
                          if seg["kind"] == "RET"), Decimal("0"))
    assert value_in - value_consumed + value_restored == value_remain


def test_layers_never_negative_or_over_received(client, demo):
    store = client.app.state.store
    for layer in store.layers(demo["run_id"]):
        remaining = Decimal(layer["qty_remaining"])
        received = Decimal(layer["qty_in"])
        assert remaining >= 0
        assert remaining <= received


def test_transfer_value_is_symmetric(client, demo):
    store = client.app.state.store
    segments = store.consumptions(demo["run_id"])
    out_value = sum((Decimal(seg["amount"]) for seg in segments if seg["kind"] == "TRN"),
                    Decimal("0"))
    layers = [layer for layer in store.layers(demo["run_id"]) if layer["origin"] == "调拨"]
    in_value = sum((Decimal(layer["qty_in"]) * Decimal(layer["unit_cost"])
                    for layer in layers), Decimal("0"))
    assert out_value == in_value == Decimal("240")


def test_count_adjustment_preserves_value_identity(client, demo):
    store = client.app.state.store
    run_id = demo["run_id"]
    layers = store.layers(run_id)
    rows = [
        {
            "count_no": item["count_no"],
            "revision": int(item["revision"]),
            "sku": item["sku"],
            "warehouse": item["warehouse"],
            "counted_qty": Decimal(item["counted_qty"]),
            "codes": [],
        }
        for item in store.counts(run_id)
        if item["status"] != "COUNT_INVALID"
    ]
    outcome = counts_mod.apply(rows, layers, None)
    adjusted_value = sum((Decimal(layer["qty_remaining"]) * Decimal(layer["unit_cost"])
                          for layer in outcome["layers"]), Decimal("0"))
    assert adjusted_value == Decimal(demo["ending_value"]) + outcome["totals"]["adjust_value"]


def test_priority_permutation_stable():
    # TRAP-19：优先级不随原因码书写顺序变化
    codes = ["QTY_PARSE", "COLUMN_COUNT", "UNIT_UNKNOWN"]
    results = {reasons.primary(list(order)) for order in itertools.permutations(codes)}
    assert results == {"COLUMN_COUNT"}


def test_rerun_keeps_row_and_total_counts(client, demo):
    store = client.app.state.store
    first = client.post("/api/demo/run").json()
    second = client.post("/api/demo/run").json()
    assert first["counts"] == second["counts"]
    assert first["quantities"] == second["quantities"]
    assert len(store.movements(demo["run_id"])) == first["counts"]["raw_rows"]
    assert len(store.counts(demo["run_id"])) == first["counts_rows"]
