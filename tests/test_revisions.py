"""版本裁决：新版判废回退旧版、整组隔离、反事实影响、留痕与幂等。

覆盖取舍两端：
- 不再「只隔离新版本、旧数量静默继续入账」：回退行打 FALLBACK_APPLIED，工作台可见；
- 不「停用整张单据」：同单据其他行照常入账，只有该业务键回退；
- 所有版本行都保留，同批重跑结论与影响完全一致。
"""
from __future__ import annotations

from decimal import Decimal

from conftest import opening_rows, read_fixture
from app import reasons
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_doc_rules import contract
from app.modules.m3_quality import revisions as revisions_mod
from app.modules.m3_quality import rules
from app.modules.m3_quality import service as m3_service
from app.modules.m4_replay import replay as replay_mod


def build_fixture(settings, name: str) -> dict:
    parsed = m1_service.read_file(
        settings, name, read_fixture(name), contract.aliases("movements"))
    return m3_service.build(settings, parsed)


def replay_with_impacts(settings, built: dict) -> dict:
    outcome = replay_mod.replay(built["rows"], opening_rows(settings), settings)
    revisions_mod.compute_impacts(
        built["decisions"], built["rows"], opening_rows(settings), settings,
        outcome["totals"])
    return outcome


def rows_by(built, doc_no: str, line_no: int = 1):
    return [row for row in built["rows"]
            if row["doc_no"] == doc_no and row["line_no"] == line_no]


def test_bad_new_revision_falls_back_to_clean_old_version(settings):
    built = build_fixture(settings, "revision_fallback.csv")
    key_rows = rows_by(built, "RK-8001")
    rejected = next(row for row in key_rows if row["revision"] == 2)
    fallback = next(row for row in key_rows if row["revision"] == 1)

    # 新版本判废进隔离，旧版本回退采纳，而不是静默沿用
    assert rejected["status"] == "quarantine"
    assert rejected["reason"] == "REVISION_REJECTED"
    assert "REVISION_REJECTED" in rejected["flags"]
    assert "QTY_MISSING" in rejected["flags"]
    assert fallback["status"] == "valid"
    assert fallback["reason"] == "FALLBACK_APPLIED"
    assert "FALLBACK_APPLIED" in fallback["flags"]
    assert built["counts"]["fallback"] == 1

    decision = built["decisions"][0]
    assert decision["outcome"] == "rejected_fallback"
    assert decision["applied_revision"] == 1
    assert decision["applied_row_no"] == fallback["row_no"]
    assert decision["rejected_row_no"] == rejected["row_no"]
    assert decision["rejected_reason"] == "QTY_MISSING"
    # 两个版本都留痕，没有丢记录
    assert {ver["revision"] for ver in decision["versions"]} == {1, 2}


def test_fallback_does_not_disable_whole_document(settings):
    # 同单据第二行是干净的，必须照常采纳，成本核算不被拖累
    built = build_fixture(settings, "revision_fallback.csv")
    other = [row for row in built["rows"]
             if row["doc_no"] == "RK-8001" and row["line_no"] == 2][0]
    assert other["status"] == "valid"
    assert other["reason"] == ""

    outcome = replay_with_impacts(settings, built)
    # 回退行 100kg + 同单第二行 50kg 都建层入账
    applied_qty = sum(
        (Decimal(layer["qty_in"]) for layer in outcome["layers"]
         if layer["doc_no"] == "RK-8001"), Decimal("0"))
    assert applied_qty == Decimal("150.0000")


def test_counterfactual_impact_when_new_revision_numeric(settings):
    # 新版本数量可用（120kg）但仓库未登记而判废：反事实差额可量化
    built = build_fixture(settings, "revision_fallback_impact.csv")
    outcome = replay_with_impacts(settings, built)
    decision = built["decisions"][0]
    assert decision["outcome"] == "rejected_fallback"
    impact = decision["impact"]
    # 若改用 120kg 的新版本，期末数量多 20kg、期末金额多 200
    assert Decimal(impact["ending_qty_delta"]) == Decimal("20.0000")
    assert Decimal(impact["ending_value_delta"]) == Decimal("200.0000")
    assert Decimal(impact["counter_ending_qty"]) == Decimal(outcome["totals"]["ending_qty"]) + Decimal("20")


def test_no_impact_when_new_revision_quantity_unusable(settings):
    # 新版本数量本身缺失：不臆造反事实数字，impact 留空
    built = build_fixture(settings, "revision_fallback.csv")
    replay_with_impacts(settings, built)
    assert built["decisions"][0]["impact"] is None


def test_rejected_all_when_no_clean_old_version(settings):
    built = build_fixture(settings, "revision_rejected_all.csv")
    assert all(row["status"] == "quarantine" for row in built["rows"])
    decision = built["decisions"][0]
    assert decision["outcome"] == "rejected_all"
    assert decision["applied_revision"] == 0
    assert built["counts"]["applied"] == 0
    assert built["counts"]["fallback"] == 0


def test_fallback_decision_is_file_order_independent(settings):
    ordered = build_fixture(settings, "revision_fallback.csv")
    shuffled = build_fixture(settings, "revision_fallback_shuffled.csv")
    first = [(d["outcome"], d["applied_revision"], d["max_revision"],
              d["rejected_reason"]) for d in ordered["decisions"]]
    second = [(d["outcome"], d["applied_revision"], d["max_revision"],
               d["rejected_reason"]) for d in shuffled["decisions"]]
    assert first == second
    assert first[0][0] == "rejected_fallback"


def test_normal_higher_revision_still_wins(settings):
    # 常规口径不回退：干净高版本覆盖低版本
    header = ("单据号,行号,类型,仓库,物料编码,数量,单位,单价,发生时间,方向,关联单号,版本\n")
    body = (
        "RK-8101,1,采购入库,WH-A,000123,100,kg,10.0000,2026-03-02T09:00:00+08:00,入,,1\n"
        "RK-8101,1,采购入库,WH-A,000123,120,kg,10.0000,2026-03-02T09:00:00+08:00,入,,2\n")
    parsed = m1_service.read_file(
        settings, "synthetic.csv", (header + body).encode("utf-8"),
        contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    newer = next(row for row in built["rows"] if row["revision"] == 2)
    older = next(row for row in built["rows"] if row["revision"] == 1)
    assert newer["status"] == "valid" and newer["qty_kg"] == Decimal("120")
    assert older["status"] == "superseded"
    assert built["decisions"][0]["outcome"] == "revised"
    assert built["counts"]["fallback"] == 0


def test_new_reason_codes_registered():
    for code in ("REVISION_REJECTED", "FALLBACK_APPLIED"):
        assert code in reasons.REASONS
        assert code in reasons.PRIORITY


def test_revisions_csv_contains_all_versions_and_impact(settings):
    built = build_fixture(settings, "revision_fallback_impact.csv")
    replay_with_impacts(settings, built)
    text = revisions_mod.csv_text(built["decisions"])
    lines = text.strip().splitlines()
    assert "裁决结论" in lines[0] and "期末数量影响kg" in lines[0]
    row = lines[1]
    assert "新版判废·回退旧版" in row
    assert "WAREHOUSE_UNKNOWN" in row
    assert "20.0000" in row


def test_rerun_produces_identical_decisions(settings):
    first = build_fixture(settings, "revision_fallback_impact.csv")
    replay_with_impacts(settings, first)
    second = build_fixture(settings, "revision_fallback_impact.csv")
    replay_with_impacts(settings, second)
    assert first["decisions"] == second["decisions"]


def test_eligibility_filter_does_not_change_default_replay(settings):
    # eligible_seq=None 与全量集合两条路径结果一致
    built = build_fixture(settings, "revision_fallback.csv")
    opening = opening_rows(settings)
    base = replay_mod.replay(built["rows"], opening, settings)
    all_seqs = {row["seq"] for row in built["rows"] if row["status"] == "valid"}
    again = replay_mod.replay(
        [dict(row) for row in built["rows"]], opening, settings,
        eligible_seq=all_seqs)
    for key in ("ending_qty", "ending_value", "cogs"):
        assert base["totals"][key] == again["totals"][key]


# ---------------- HTTP 层 ----------------

def test_revisions_api_and_csv(client, demo):
    payload = client.get("/api/revisions").json()
    assert payload["run_id"] == demo["run_id"]
    assert payload["summary"]["total"] == len(payload["decisions"])
    assert {item["code"] for item in payload["summary"]["outcome_counts"]} <= \
        set(revisions_mod.OUTCOME_LABELS)

    response = client.get("/api/revisions.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "裁决结论" in response.text.splitlines()[0]


def test_export_revisions_csv(client, demo):
    response = client.get("/api/export/revisions.csv")
    assert response.status_code == 200
    assert "revisions-" in response.headers["content-disposition"]
    listed = client.get("/api/export").json()
    kinds = {item["kind"] for item in listed["files"]}
    assert "revisions.csv" in kinds
    assert listed["counts"]["fallback"] == demo["counts"]["fallback"]


def test_revisions_page_renders(client, demo):
    response = client.get("/revisions")
    assert response.status_code == 200
    assert "版本裁决" in response.text


def test_quarantine_csv_has_flags_column(client, demo):
    text = client.get("/api/quarantine.csv").text
    assert "标记" in text.splitlines()[0]


def test_manifest_records_fallback_count(client, demo):
    manifest = client.get("/api/export/manifest.json").json()
    assert "fallback" in manifest["counts"]
    assert manifest["counts"]["fallback"] == demo["counts"]["fallback"]


def test_fallback_scenario_end_to_end(client, settings):
    data = read_fixture("revision_fallback_impact.csv")
    response = client.post(
        "/api/movements/import",
        files={"file": ("revision_fallback_impact.csv", data, "text/csv")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"]["fallback"] == 1

    decisions = client.get("/api/revisions").json()["decisions"]
    decision = next(d for d in decisions if d["doc_no"] == "RK-8002")
    assert decision["outcome"] == "rejected_fallback"
    assert Decimal(decision["impact"]["ending_qty_delta"]) == Decimal("20.0000")

    # 隔离区能按新原因码筛出判废新版本
    filtered = client.get("/api/quarantine?reason=REVISION_REJECTED").json()
    assert filtered["count"] >= 1
    assert all(row["reason"] == "REVISION_REJECTED" for row in filtered["rows"])

    # 重跑同一文件：run_id 与裁决结论保持一致
    rerun = client.post(
        "/api/movements/import",
        files={"file": ("revision_fallback_impact.csv", data, "text/csv")},
    ).json()
    assert rerun["run_id"] == payload["run_id"]
    again = client.get("/api/revisions").json()["decisions"]
    assert again == decisions


def test_rules_build_exposes_fallback_count(settings):
    built = build_fixture(settings, "revision_fallback.csv")
    assert "fallback" in built["counts"]
    assert isinstance(rules.FALLBACK_FLAG, str)


def test_three_revision_chain_skips_bad_middle_to_old_clean(settings):
    header = ("单据号,行号,类型,仓库,物料编码,数量,单位,单价,发生时间,方向,关联单号,版本\n")
    body = (
        "RK-9001,1,采购入库,WH-A,000123,100,kg,10.0,2026-03-02T09:00:00+08:00,入,,1\n"
        "RK-9001,1,采购入库,WH-A,000123,0,kg,10.0,2026-03-02T09:00:00+08:00,入,,2\n"
        "RK-9001,1,采购入库,WH-A,000123,-,kg,10.0,2026-03-02T09:00:00+08:00,入,,3\n")
    parsed = m1_service.read_file(settings, "chain.csv", (header + body).encode("utf-8"),
                                  contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    decision = built["decisions"][0]
    # v3 判废、v2 也脏，跳过 v2 回退到干净的 v1
    assert decision["outcome"] == "rejected_fallback"
    assert decision["applied_revision"] == 1
    statuses = {row["revision"]: (row["status"], row["reason"]) for row in built["rows"]}
    assert statuses[3][0] == "quarantine" and statuses[3][1] == "REVISION_REJECTED"
    assert statuses[2][0] == "quarantine"
    # 行数守恒，没有记录丢失
    c = built["counts"]
    assert c["raw_rows"] == (c["applied"] + c["quarantined"]
                             + c["superseded"] + c["duplicate"])


def test_fallback_picks_nearest_clean_revision(settings):
    header = ("单据号,行号,类型,仓库,物料编码,数量,单位,单价,发生时间,方向,关联单号,版本\n")
    body = (
        "RK-9002,1,采购入库,WH-A,000123,100,kg,10.0,2026-03-02T09:00:00+08:00,入,,1\n"
        "RK-9002,1,采购入库,WH-A,000123,110,kg,10.0,2026-03-02T09:00:00+08:00,入,,2\n"
        "RK-9002,1,采购入库,WH-A,000123,-,kg,10.0,2026-03-02T09:00:00+08:00,入,,3\n")
    parsed = m1_service.read_file(settings, "nearest.csv", (header + body).encode("utf-8"),
                                  contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    assert built["decisions"][0]["applied_revision"] == 2
    v1 = next(row for row in built["rows"] if row["revision"] == 1)
    assert v1["status"] == "superseded"
