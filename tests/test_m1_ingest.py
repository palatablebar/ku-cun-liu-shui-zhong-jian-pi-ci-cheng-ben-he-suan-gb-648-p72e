"""M1 采信：编码回退、BOM、分段、列数、截断末行、原始行号与列名演进。"""
from __future__ import annotations

from conftest import read_fixture, read_sample
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_doc_rules import contract
from app.modules.m3_quality import service as m3_service

HEADER = "单据号,行号,类型,仓库,物料编码,数量,单位,单价,发生时间,方向,关联单号,版本"


def read_raw(settings, name: str, raw: bytes) -> dict:
    return m1_service.read_file(settings, name, raw, contract.aliases("movements"))


def read_sample_file(settings, name: str) -> dict:
    return read_raw(settings, name, read_sample(name))


def test_gb18030_fallback_keeps_header(settings):
    # TRAP-01：编码混用，直接 open() 会崩或列名乱码
    row = "RK-6001,1,采购入库,WH-A,000123,10,kg,10.0000,2026-06-01T09:00:00+08:00,入,,1"
    raw = (HEADER + "\n" + row + "\n").encode("gb18030")
    parsed = read_raw(settings, "gb18030.csv", raw)
    assert parsed["encoding"] == "gb18030"
    assert parsed["header"][0] == "单据号"
    assert len(parsed["records"]) == 1


def test_bom_stripped_from_first_header(settings):
    # TRAP-02：首列名带 \ufeff，按列名取值会全空
    raw = ("\ufeff" + HEADER + "\nRK-6002,1,采购入库,WH-A,000123,10,kg,10,"
           "2026-06-01T09:00:00+08:00,入,,1\n").encode("utf-8")
    parsed = read_raw(settings, "bom.csv", raw)
    assert parsed["bom"] is True
    assert parsed["header"][0] == "单据号"
    assert parsed["records"][0]["cells"]["doc_no"] == "RK-6002"


def test_repeated_header_counts_segments(settings):
    # TRAP-04：分段导出里的重复表头不是数据行
    parsed = read_sample_file(settings, "movements_dirty.csv")
    assert parsed["segments"] == 2
    assert parsed["header"][0] == "单据号"
    assert len(parsed["records"]) == 34


def test_empty_and_header_only_states(settings):
    assert read_sample_file(settings, "empty.csv")["state"] == "EMPTY_FILE"
    assert read_sample_file(settings, "movements_header_only.csv")["state"] == "HEADER_ONLY"
    blank = read_raw(settings, "blank.csv", (HEADER + "\n\n\n").encode("utf-8"))
    assert blank["state"] == "ALL_BLANK"
    assert blank["blank_rows"] == 2


def test_column_count_row_is_flagged(settings):
    # TRAP-05：列数不齐的行不能被静默补齐
    parsed = read_sample_file(settings, "movements_dirty.csv")
    extra = [record for record in parsed["records"] if record["row_no"] == 28]
    assert extra and extra[0]["column_ok"] is False
    built = m3_service.build(settings, parsed)
    row = [item for item in built["rows"] if item["row_no"] == 28][0]
    assert row["status"] == "quarantine"
    assert row["reason"] == "COLUMN_COUNT"


def test_truncated_tail_rejected(settings):
    # TRAP-16：末行无换行且列数不足
    parsed = read_sample_file(settings, "movements_dirty.csv")
    assert parsed["records"][-1]["truncated"] is True
    built = m3_service.build(settings, parsed)
    tail = built["rows"][-1]
    assert tail["reason"] == "TRUNCATED_TAIL"
    assert tail["status"] == "quarantine"
    truncated = read_sample_file(settings, "movements_truncated.csv")
    assert truncated["records"][0]["truncated"] is True


def test_quoted_field_stays_one_record(settings):
    # 引号内含换行时 splitlines() 会把一条记录切成两条
    parsed = read_raw(settings, "quoted_field.csv", read_fixture("quoted_field.csv"))
    assert len(parsed["records"]) == 1
    assert "\n" in parsed["records"][0]["cells"]["ref_doc"]


def test_legacy_column_names_mapped(settings):
    parsed = read_raw(settings, "legacy_columns.csv", read_fixture("legacy_columns.csv"))
    assert parsed["records"][0]["cells"]["doc_no"] == "RK-5101"
    assert parsed["records"][0]["cells"]["sku"] == "000123"
    built = m3_service.build(settings, parsed)
    assert built["rows"][0]["status"] == "valid"


def test_original_row_numbers_preserved(settings):
    parsed = read_sample_file(settings, "movements_dirty.csv")
    assert parsed["records"][0]["row_no"] == 2
    assert [record["row_no"] for record in parsed["records"]][:5] == [2, 3, 4, 5, 7]
    assert parsed["records"][10]["row_no"] == 13
