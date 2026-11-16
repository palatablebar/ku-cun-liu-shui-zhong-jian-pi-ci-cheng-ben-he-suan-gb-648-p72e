"""质量规则：解析码定级、同一业务键的版本裁决、多种命中取唯一原因码。

裁决规则（确定性，与文件顺序无关的部分）：
- 同一 `(单据号, 行号)` 只保留最高版本那一行；
- 最高版本出现两个不同取值 → 整组按 `DUP_DOC_CONFLICT` 隔离，谁都不信；
- 最高版本只有一种取值 → 保留文件里最先出现的那行，其余同版本同值记重复；
- 低版本行记被取代（不计入采纳、也不算隔离）。
"""
from __future__ import annotations

import hashlib
import json

from ... import reasons


def fingerprint(row: dict) -> str:
    payload = json.dumps(
        {"cells": sorted(row.get("cells", {}).items()), "raw": row.get("raw", [])},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _reason(row: dict) -> str:
    return reasons.primary(row["codes"]) or ""


def dedupe(rows: list[dict]) -> dict:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["codes"]:
            continue
        key = (row["doc_no"], row["line_no"])
        groups.setdefault(key, []).append(row)

    for items in groups.values():
        by_revision: dict[int, list[dict]] = {}
        for row in items:
            by_revision.setdefault(row["revision"], []).append(row)
        max_revision = max(by_revision)
        contenders = by_revision[max_revision]
        distinct = {fingerprint(row) for row in contenders}
        if len(distinct) > 1:
            for row in items:
                row["status"] = "quarantine"
                row["reason"] = "DUP_DOC_CONFLICT"
                row["flags"] = ["REVISION_CONFLICT"]
            continue
        kept = contenders[0]
        for row in items:
            if row is kept:
                row["status"] = "valid"
                row["reason"] = ""
            elif row["revision"] == max_revision:
                row["status"] = "duplicate"
                row["reason"] = "DUPLICATE_ROW"
            else:
                row["status"] = "superseded"
                row["reason"] = "SUPERSEDED"
    return groups


def build(rows: list[dict]) -> dict:
    """给每一行定状态：valid / quarantine / duplicate / superseded。"""
    for row in rows:
        row["status"] = "valid"
        row["reason"] = ""
        row["flags"] = []
        row["applied_seq"] = 0
        if row["codes"]:
            row["status"] = "quarantine"
            row["reason"] = _reason(row)

    dedupe(rows)

    applied = 0
    for row in rows:
        if row["status"] == "valid":
            applied += 1
            row["applied_seq"] = applied

    counts = {
        "raw_rows": len(rows),
        "applied": sum(1 for row in rows if row["status"] == "valid"),
        "quarantined": sum(1 for row in rows if row["status"] == "quarantine"),
        "superseded": sum(1 for row in rows if row["status"] == "superseded"),
        "duplicate": sum(1 for row in rows if row["status"] == "duplicate"),
    }
    return {"rows": rows, "counts": counts}


def status_counts(rows: list[dict]) -> dict:
    counts = {"valid": 0, "quarantine": 0, "duplicate": 0, "superseded": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts
