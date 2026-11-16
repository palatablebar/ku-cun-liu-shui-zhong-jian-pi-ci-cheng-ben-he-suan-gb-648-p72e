"""M4 时序重放：全序、超库存拒绝后继续、调拨双边、FIFO 消耗、退货回原层。"""
from __future__ import annotations

from decimal import Decimal

from conftest import (build_rows, fixture_json, opening_rows, parse_sample,
                      read_fixture, replay_sample)
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_doc_rules import contract
from app.modules.m3_quality import service as m3_service
from app.modules.m4_replay import replay as replay_mod

EXPECTED = fixture_json("expected_totals.json")
EXPECTED_LAYERS = fixture_json("expected_layers.json")


def layer(outcome: dict, layer_id: str) -> dict:
    return [item for item in outcome["layers"] if item["layer_id"] == layer_id][0]


def rows_by_doc(outcome_rows, doc_no: str):
    return [row for row in outcome_rows if row["doc_no"] == doc_no]


def run_fixture(settings, name: str) -> tuple[dict, list[dict]]:
    parsed = m1_service.read_file(
        settings, name, read_fixture(name), contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    outcome = replay_mod.replay(built["rows"], opening_rows(settings), settings)
    return outcome, built["rows"]


def test_replay_totals_match_expected(settings):
    parsed = parse_sample(settings)
    built = build_rows(settings, parsed)
    outcome = replay_mod.replay(built["rows"], opening_rows(settings), settings)
    totals = outcome["totals"]
    assert format(totals["ending_qty"], "f") == EXPECTED["quantities"]["ending_qty"]
    assert format(totals["cogs"], "f").startswith(EXPECTED["cogs"][:6])
    assert len(outcome["layers"]) == EXPECTED["layers"]
    assert format(outcome["layers"][0]["qty_remaining"], "f") == "100.0000"


def test_replay_totals_survive_input_permutation(settings):
    # TRAP-13：文件乱序不影响重放，全序按发生时间排
    baseline = replay_sample(settings)
    indices = list(range(34))
    shuffled = list(reversed(indices))
    other = replay_sample(settings, shuffle=shuffled)
    for key in ("opening_qty", "in_qty", "out_qty", "adj_in_qty", "adj_out_qty",
                "ending_qty", "cogs", "ending_value"):
        assert baseline["totals"][key] == other["totals"][key]
    under_key = {(item["sku"], item["warehouse"]): item["qty_remaining"]
                 for item in baseline["layers"]}
    same_key = {(item["sku"], item["warehouse"]): item["qty_remaining"]
                for item in other["layers"]}
    assert under_key == same_key


def test_out_exceeds_stock_rejected_then_replay_continues(settings):
    outcome = replay_sample(settings)
    rows = outcome["rows"]
    rejected = [row for row in rows if row["reason"] == "OUT_EXCEEDS_STOCK"]
    assert {row["row_no"] for row in rejected} == {7, 35}
    assert all(row["status"] == "quarantine" for row in rejected)
    later = [row for row in rows if row["doc_no"] == "RK-2019" and row["status"] == "valid"]
    assert later and later[0]["applied_seq"] > 0
    assert outcome["totals"]["ending_qty"] > 0


def test_transfer_pair_creates_layer_in_target(settings):
    # TRAP-21：调拨必须双边，成对时出边消耗、入边按消耗成本建层
    outcome = replay_sample(settings)
    target = layer(outcome, "L000008")
    assert target["warehouse"] == "WH-C"
    assert target["sku"] == "000456"
    assert target["unit_cost"] == Decimal("8.0000")
    assert target["qty_in"] == Decimal("30.0000")
    assert outcome["pairs"] == 1


def test_transfer_missing_leg_quarantined(settings):
    # TRAP-21：只有一条边的调拨整组隔离
    outcome = replay_sample(settings)
    broken = [row for row in outcome["rows"] if row["doc_no"] == "DB-2006"]
    assert broken and broken[0]["reason"] == "TRN_MISSING_LEG"
    assert broken[0]["status"] == "quarantine"
    fixture_outcome, fixture_rows = run_fixture(settings, "transfer_single_leg.csv")
    assert fixture_rows[0]["reason"] == "TRN_MISSING_LEG"
    assert fixture_outcome["totals"]["trn_in_qty"] == 0


def test_transfer_quantity_mismatch_quarantined(settings):
    outcome = replay_sample(settings)
    legs = [row for row in outcome["rows"] if row["doc_no"] == "DB-2027"]
    assert len(legs) == 2
    assert {row["reason"] for row in legs} == {"TRN_QTY_MISMATCH"}
    assert {row["status"] for row in legs} == {"quarantine"}


def test_fifo_consumes_earliest_layers(settings):
    outcome = replay_sample(settings)
    segments = [seg for seg in outcome["segments"] if seg["out_doc"] == "CK-2002"]
    assert [seg["layer_id"] for seg in segments] == ["L000001", "L000005"]
    assert [seg["qty"] for seg in segments] == [Decimal("100"), Decimal("800")]
    assert sum((seg["amount"] for seg in segments), Decimal("0")) == Decimal("9000")


def test_return_with_original_restores_layer(settings):
    outcome = replay_sample(settings)
    restored = [seg for seg in outcome["segments"] if seg["kind"] == "RET"]
    assert len(restored) == 1
    assert restored[0]["layer_id"] == "L000001"
    assert restored[0]["qty"] == Decimal("100")
    assert layer(outcome, "L000001")["qty_remaining"] == Decimal("100.0000")
    assert restored[0]["amount"] == Decimal("1000")
    assert outcome["totals"]["return_value"] == Decimal("1095")


def test_return_without_original_creates_new_layer(settings):
    outcome = replay_sample(settings)
    new_layer = layer(outcome, "L000009")
    assert new_layer["origin"] == "退货入库"
    assert new_layer["unit_cost"] == Decimal("9.5000")
    assert new_layer["qty_in"] == Decimal("10.0000")


def test_return_edge_cases(settings):
    outcome, rows = run_fixture(settings, "return_cases.csv")
    status = {row["doc_no"]: (row["status"], row["reason"]) for row in rows}
    assert status["TH-5403"] == ("valid", "")
    assert status["TH-5404"] == ("quarantine", "RETURN_EXCEEDS_ORIGINAL")
    assert status["TH-5405"] == ("quarantine", "RETURN_DOC_NOT_FOUND")
    assert layer(outcome, "L000001")["qty_remaining"] == Decimal("80.0000")
