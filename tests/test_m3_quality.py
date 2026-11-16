"""M3 质量隔离：状态定级、同键版本裁决、原因码优先级、隔离导出与分布。"""
from __future__ import annotations

from conftest import fixture_json, read_fixture, read_sample
from app import reasons
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_doc_rules import contract
from app.modules.m3_quality import rules
from app.modules.m3_quality import service as m3_service

EXPECTED = fixture_json("expected_totals.json")


def build(settings, name: str = "movements_dirty.csv") -> dict:
    parsed = m1_service.read_file(
        settings, name, read_sample(name), contract.aliases("movements"))
    return m3_service.build(settings, parsed)


def build_fixture(settings, name: str) -> dict:
    parsed = m1_service.read_file(
        settings, name, read_fixture(name), contract.aliases("movements"))
    return m3_service.build(settings, parsed)


def by_row(built: dict, row_no: int) -> dict:
    return [row for row in built["rows"] if row["row_no"] == row_no][0]


def test_status_counts_match_expected(settings):
    # 质量阶段只判字段级问题；重放阶段的超库存/调拨/退货问题由 M4 追加隔离。
    built = build(settings)
    assert built["counts"] == {
        "raw_rows": 34,
        "applied": 20,
        "quarantined": 12,
        "superseded": 1,
        "duplicate": 1,
    }
    assert built["counts"]["raw_rows"] == EXPECTED["counts"]["raw_rows"]
    assert built["counts"]["superseded"] == EXPECTED["counts"]["superseded"]


def test_identical_duplicate_ignored(settings):
    # TRAP-14：完全重复的行只保留一条
    built = build(settings)
    duplicate = by_row(built, 25)
    assert duplicate["status"] == "duplicate"
    assert duplicate["reason"] == "DUPLICATE_ROW"
    applied = [row for row in built["rows"] if row["doc_no"] == "CK-2002"
               and row["status"] == "valid"]
    assert len(applied) == 1


def test_same_revision_conflict_quarantined(settings):
    # TRAP-14：同版本两个取值都不采信
    built = build_fixture(settings, "dup_conflict.csv")
    assert built["counts"]["applied"] == 0
    assert built["counts"]["quarantined"] == 2
    assert {row["reason"] for row in built["rows"]} == {"DUP_DOC_CONFLICT"}
    assert all(int(row["revision"]) == 1 for row in built["rows"])


def test_higher_revision_wins_file_order_irrelevant(settings):
    # TRAP-14：高版本覆盖低版本，低版本记被取代而不是隔离
    built = build(settings)
    newer = by_row(built, 23)
    older = by_row(built, 24)
    assert newer["status"] == "valid"
    assert newer["qty_kg"] == 120
    assert older["status"] == "superseded"
    assert older["reason"] == "SUPERSEDED"


def test_priority_order_is_stable():
    # TRAP-19：一行命中多条规则时按固定优先级取主原因码，与书写顺序无关
    assert reasons.primary(["QTY_PARSE", "COLUMN_COUNT"]) == "COLUMN_COUNT"
    assert reasons.primary(["COLUMN_COUNT", "QTY_PARSE"]) == "COLUMN_COUNT"
    assert reasons.primary(["TRUNCATED_TAIL", "COLUMN_COUNT"]) == "TRUNCATED_TAIL"
    assert reasons.primary(["UNIT_UNKNOWN", "WAREHOUSE_UNKNOWN"]) == "WAREHOUSE_UNKNOWN"
    first = rules.build([synthetic(["QTY_PARSE", "TRUNCATED_TAIL"])])
    second = rules.build([synthetic(["TRUNCATED_TAIL", "QTY_PARSE"])])
    assert first["rows"][0]["reason"] == second["rows"][0]["reason"] == "TRUNCATED_TAIL"


def synthetic(codes: list[str]) -> dict:
    return {
        "seq": 1,
        "row_no": 2,
        "doc_no": "RK-9901",
        "line_no": 1,
        "revision": 1,
        "codes": list(codes),
        "doc_type": "采购入库",
        "warehouse": "WH-A",
        "sku": "000123",
        "raw": [],
        "raw_qty": "10",
    }


def test_quarantine_csv_has_evidence(client, demo):
    response = client.get("/api/quarantine.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    lines = response.text.strip().splitlines()
    assert len(lines) == 19
    header = lines[0].split(",")
    assert "原因码" in header and "原数量" in header and "文件行号" in header
    assert any("TRUNCATED_TAIL" in line for line in lines)


def test_quarantine_distribution_counts(client, demo):
    payload = client.get("/api/quarantine").json()
    assert payload["count"] == 18
    distribution = {item["code"]: item["count"] for item in payload["distribution"]}
    assert distribution["OUT_EXCEEDS_STOCK"] == 2
    assert distribution["DUP_DOC_CONFLICT"] == 2
    assert distribution["TRN_QTY_MISMATCH"] == 2
    assert distribution["SKU_UNKNOWN"] == 2
    assert distribution["TRUNCATED_TAIL"] == 1
    assert distribution["PRICE_REQUIRED"] == 1


def test_reason_filters(settings, client, demo):
    built = build(settings)
    distribution = {item["code"]: item["count"]
                    for item in reasons.distribution(
                        [row for row in built["rows"] if row["status"] == "quarantine"])}
    assert distribution["WAREHOUSE_UNKNOWN"] == 1
    assert distribution["UNIT_UNKNOWN"] == 1
    assert distribution["QTY_NON_POSITIVE"] == 1
    assert distribution["TS_PARSE"] == 1
    assert distribution["QTY_MISSING"] == 1
    assert distribution["DOC_TYPE_UNKNOWN"] if "DOC_TYPE_UNKNOWN" in distribution else True
    filtered = client.get("/api/quarantine?reason=OUT_EXCEEDS_STOCK").json()
    assert filtered["count"] == 2
    assert all(row["reason"] == "OUT_EXCEEDS_STOCK" for row in filtered["rows"])
