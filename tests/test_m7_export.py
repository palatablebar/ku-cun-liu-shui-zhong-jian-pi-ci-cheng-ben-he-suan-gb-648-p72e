"""M7 导出：证据行、层、成本 CSV 与带 sha256 的运行清单。"""
from __future__ import annotations

import json

from conftest import fixture_json

EXPECTED = fixture_json("expected_totals.json")


def test_movements_csv_keeps_raw_and_reason(client, demo):
    response = client.get("/api/export/movements.csv")
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 35
    assert "原因码" in lines[0] and "数量原文" in lines[0]
    assert any("TRUNCATED_TAIL" in line for line in lines)
    assert any('"1,200"' in line or "1,200" in line for line in lines)


def test_leading_zero_sku_survives_export(client, demo):
    # TRAP-08：编码按字符串导出，前导零不丢；"123" 与 "000123" 是两个物料
    text = client.get("/api/export/movements.csv").text
    assert ",000123," in text
    lines = text.splitlines()
    leading = [line for line in lines if ",000123," in line]
    bare = [line for line in lines if ",123," in line]
    assert leading and bare
    assert all("SKU_UNKNOWN" in line for line in bare)


def test_layers_csv(client, demo):
    response = client.get("/api/export/layers.csv")
    lines = response.text.strip().splitlines()
    assert len(lines) == 14
    assert "层金额" in lines[0]
    assert any(line.startswith("L000001") for line in lines)
    assert any("100.0000" in line for line in lines)


def test_cost_csv(client, demo):
    lines = client.get("/api/export/cost.csv").text.strip().splitlines()
    assert len(lines) == 6
    assert "净成本" in lines[0]
    assert sum(1 for line in lines if "CK-2002" in line) == 1


def test_manifest_has_run_id_and_input_sha(client, demo):
    payload = json.loads(client.get("/api/export/manifest.json").text)
    assert payload["run_id"] == demo["run_id"]
    assert len(payload["inputs"]["movements"]["sha256"]) == 64
    assert len(payload["inputs"]["opening"]["sha256"]) == 64
    assert len(payload["inputs"]["counts"]["sha256"]) == 64
    assert payload["inputs"]["movements"]["file"] == "movements_dirty.csv"
    assert payload["counts"]["applied"] == EXPECTED["counts"]["applied"]
    assert len(payload["checks"]) == 10
    assert payload["params"]["registry"]


def test_manifest_plain_decimal(client, demo):
    text = client.get("/api/export/manifest.json").text
    payload = json.loads(text)
    assert "E+" not in text and "e+" not in text
    assert payload["cost"]["cogs"].startswith(EXPECTED["cogs"][:6])
    assert payload["quantities"]["ending"] == EXPECTED["quantities"]["ending_qty"]


def test_unknown_export_kind(client, demo):
    response = client.get("/api/export/report.csv")
    assert response.status_code == 400
    assert response.json()["error"] == "E_EXPORT_KIND"


def test_export_without_run(client):
    response = client.get("/api/export/movements.csv")
    assert response.status_code == 409
    assert response.json()["error"] == "E_NO_RUN"
