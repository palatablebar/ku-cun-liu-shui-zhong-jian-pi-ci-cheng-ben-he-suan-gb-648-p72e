"""M6 盘点对账：六本账、调整层、版本闸门与提交校验。"""
from __future__ import annotations

from decimal import Decimal

from conftest import fixture_json

EXPECTED = fixture_json("expected_totals.json")
STALE = fixture_json("stale_counts.json")


def report(client) -> dict:
    return client.get("/api/reconcile").json()


def body(key: str) -> dict:
    payload = STALE[key]
    return {
        "count_no": payload["count_no"],
        "revision": payload["revision"],
        "rows": payload["rows"],
    }


def test_six_books_all_pass(client, demo):
    payload = report(client)
    assert payload["ok"] is True
    assert len(payload["checks"]) == 10
    assert {item["code"] for item in payload["checks"]} == {
        "rows", "quantity", "layer_qty", "layer_value", "conservation_value",
        "conservation_qty", "cogs", "transfer", "counts_rows", "counts",
    }


def test_quantity_identity_numbers(client, demo):
    totals = report(client)["totals"]
    assert totals["opening_qty"] == EXPECTED["quantities"]["opening_qty"]
    assert totals["in_qty"] == EXPECTED["quantities"]["in_qty"]
    assert totals["out_qty"] == EXPECTED["quantities"]["out_qty"]
    assert totals["ending_qty"] == EXPECTED["quantities"]["ending_qty"]
    assert totals["cogs"] == EXPECTED["cogs"]
    assert totals["adjusted_qty"] == EXPECTED["adjusted_qty"]


def test_count_rows_and_adjustments(client, demo):
    payload = report(client)
    rows = {row["count_no"] + row["sku"]: row for row in payload["counts"]}
    assert len(payload["counts"]) == 5
    assert Decimal(rows["PD-C-01000123"]["diff_qty"]) == Decimal("-1.2")
    assert Decimal(rows["PD-C-01000456"]["diff_qty"]) == Decimal("2.5")
    assert rows["PD-C-02000999"]["status"] == "COUNT_BALANCED"
    assert Decimal(payload["adjust"]["adjust_in_qty"]) == Decimal("2.5")
    assert Decimal(payload["adjust"]["adjust_out_qty"]) == Decimal("3.2")
    assert Decimal(payload["adjust"]["adjust_value"]) == Decimal("-12")


def test_reference_cost_for_positive_diff(client, demo):
    payload = report(client)
    row = [item for item in payload["counts"]
           if item["count_no"] == "PD-C-01" and item["sku"] == "000456"][0]
    assert Decimal(row["adjust_value"]) == Decimal("20")


def test_stale_revision_rejected(client, demo):
    # TRAP-26：旧版本提交不能覆盖新数据
    accepted = client.post("/api/reconcile/counts", json={
        "count_no": STALE["count_no"],
        "revision": STALE["revision"],
        "rows": STALE["rows"],
    })
    assert accepted.status_code == 200, accepted.text
    stale = client.post("/api/reconcile/counts", json=body("stale"))
    assert stale.status_code == 409
    assert stale.json()["error"] == "E_STALE_COUNT_REVISION"


def test_same_revision_same_payload_idempotent(client, demo):
    payload = {
        "count_no": STALE["count_no"],
        "revision": STALE["revision"],
        "rows": STALE["rows"],
    }
    assert client.post("/api/reconcile/counts", json=payload).status_code == 200
    again = client.post("/api/reconcile/counts", json=payload)
    assert again.status_code == 200
    counts = report(client)["counts"]
    assert [row["count_no"] for row in counts].count(STALE["count_no"]) == 1


def test_same_revision_different_payload_conflict(client, demo):
    payload = {
        "count_no": STALE["count_no"],
        "revision": STALE["revision"],
        "rows": STALE["rows"],
    }
    assert client.post("/api/reconcile/counts", json=payload).status_code == 200
    conflict = client.post("/api/reconcile/counts", json=body("conflict"))
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "E_COUNT_REVISION_CONFLICT"


def test_count_short_rejected_and_reported(client, demo):
    first = {"count_no": "PD-S-1", "revision": 1,
             "rows": [{"sku": "000456", "warehouse": "WH-C",
                       "counted_qty": "0", "unit": "kg"}]}
    assert client.post("/api/reconcile/counts", json=first).status_code == 200
    second = {"count_no": "PD-S-2", "revision": 1,
              "rows": [{"sku": "000456", "warehouse": "WH-C",
                        "counted_qty": "0", "unit": "kg"}]}
    assert client.post("/api/reconcile/counts", json=second).status_code == 200
    rows = report(client)["counts"]
    short = [row for row in rows if row["status"] == "COUNT_SHORT"]
    assert len(short) == 1
    assert short[0]["reason"] == "ADJUST_EXCEEDS_STOCK"


def test_submit_validation(client, demo):
    missing = client.post("/api/reconcile/counts", json={"revision": 1, "rows": []})
    assert missing.status_code == 400
    bad_sku = client.post("/api/reconcile/counts", json={
        "count_no": "PD-Z-1", "revision": 1,
        "rows": [{"sku": "999999", "warehouse": "WH-A", "counted_qty": "1"}]})
    assert bad_sku.status_code == 400
    duplicated = client.post("/api/reconcile/counts", json={
        "count_no": "PD-Z-2", "revision": 1,
        "rows": [
            {"sku": "000123", "warehouse": "WH-A", "counted_qty": "1"},
            {"sku": "000123", "warehouse": "WH-A", "counted_qty": "2"},
        ]})
    assert duplicated.status_code == 400
