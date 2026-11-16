"""隔离区：原因码分布、可导出的证据行。

导出保留文件行号、单据键、原数量原文、原单位与原因码，人工复核不需要读源码。
"""
from __future__ import annotations

import csv
import io

from ... import reasons

HEADER = (
    "文件行号", "单据号", "单据行号", "类型", "仓库", "物料编码",
    "原因码", "原因", "原数量", "原单位", "原单价", "原时间", "版本",
)


def distribution(rows) -> list[dict]:
    return reasons.distribution(rows)


def csv_text(rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            [
                row.get("row_no", ""),
                row.get("doc_no", ""),
                row.get("line_no", 0),
                row.get("doc_type", ""),
                row.get("warehouse", ""),
                row.get("sku", ""),
                row.get("reason", ""),
                reasons.label(row.get("reason", "")),
                row.get("raw_qty", ""),
                row.get("raw_unit", ""),
                row.get("raw_cost", ""),
                row.get("raw_ts", ""),
                row.get("revision", 1),
            ]
        )
    return buffer.getvalue()


def totals(rows) -> dict:
    from decimal import Decimal

    total = Decimal("0")
    for row in rows:
        try:
            total += Decimal(str(row.get("raw_qty") or "0"))
        except Exception:  # 原值可能带千分位，这里只做展示用粗合计
            continue
    return {"rows": len(rows), "raw_qty_sum": format(total, "f")}
