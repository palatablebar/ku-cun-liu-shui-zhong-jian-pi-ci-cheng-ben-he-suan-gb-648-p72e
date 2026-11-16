"""端到端：样例 → 采信 → 质量 → 重放 → 成本 → 盘点 → 导出，含幂等与失败输入。"""
from __future__ import annotations

from decimal import Decimal

from conftest import fixture_json

EXPECTED = fixture_json("expected_totals.json")
PAGES = ("/", "/movements", "/layers", "/cost", "/reconcile", "/export")


def test_all_pages_and_health(client, demo):
    for page in PAGES:
        response = client.get(page)
        assert response.status_code == 200, page
        assert "stockflow" in response.text.lower() or page in response.text
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["run_id"] == demo["run_id"]
    assert health["status_of_run"] == "ready"


def test_main_flow_numbers(client, demo):
    assert demo["counts"] == EXPECTED["counts"]
    assert demo["quantities"] == EXPECTED["quantities"]
    assert Decimal(demo["cogs"]) == Decimal(EXPECTED["cogs"])
    assert Decimal(demo["ending_value"]) == Decimal(EXPECTED["ending_value"])
    assert demo["layers"] == EXPECTED["layers"]
    assert demo["segments"] == EXPECTED["segments"]
    assert demo["reconcile_ok"] is True
    quarantine = client.get("/api/quarantine").json()
    assert quarantine["count"] == EXPECTED["counts"]["quarantined"]


def test_rerun_same_run_id_and_same_rows(client, demo):
    store = client.app.state.store
    before_rows = len(store.movements(demo["run_id"]))
    layers_before = len(store.layers(demo["run_id"]))
    again = client.post("/api/demo/run").json()
    assert again["run_id"] == demo["run_id"]
    assert again["quantities"] == demo["quantities"]
    assert again["cogs"] == demo["cogs"]
    assert again["counts"] == demo["counts"]
    assert len(store.movements(demo["run_id"])) == before_rows
    assert len(store.layers(demo["run_id"])) == layers_before


def test_clean_sample_uses_same_path(client):
    response = client.post("/api/movements/import",
                           data={"sample": "movements_clean.csv"})
    assert response.status_code == 200, response.text
    payload = response.json()
    # 期初四个层都在，清样例只走同一条重放路径
    assert Decimal(payload["quantities"]["ending_qty"]) == Decimal("252.5")
    assert Decimal(payload["cogs"]) == Decimal("600")
    health = client.get("/api/health").json()
    assert health["status_of_run"] == "ready"


def test_truncated_sample_isolated(client):
    response = client.post("/api/movements/import",
                           data={"sample": "movements_truncated.csv"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["quarantined"] == 1
    assert payload["counts"]["applied"] == 0
    assert payload["status"] == "empty"
    quarantine = client.get("/api/quarantine").json()
    assert quarantine["rows"][0]["reason"] == "TRUNCATED_TAIL"
    assert client.get("/api/health").json()["status_of_run"] == "empty"


def test_failure_paths(client, demo):
    missing = client.post("/api/movements/import", data={"sample": "no_such.csv"})
    assert missing.status_code == 404
    bad_body = client.post("/api/reconcile/counts", json={"count_no": "", "rows": []})
    assert bad_body.status_code == 400
    stale = client.post("/api/reconcile/counts", json={
        "count_no": "PD-E-1", "revision": 1,
        "rows": [{"sku": "000123", "warehouse": "WH-A", "counted_qty": "10"}]})
    assert stale.status_code == 200
    older = client.post("/api/reconcile/counts", json={
        "count_no": "PD-E-1", "revision": 0,
        "rows": [{"sku": "000123", "warehouse": "WH-A", "counted_qty": "10"}]})
    assert older.status_code == 400
