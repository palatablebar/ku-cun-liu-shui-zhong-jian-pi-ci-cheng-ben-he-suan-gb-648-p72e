"""质量规则：解析码定级、同一业务键的版本裁决、多种命中取唯一原因码。

裁决规则（确定性，与文件顺序无关，同批重跑结果一致）：
- 同一 `(单据号, 行号)` 为一个业务键，组内每个版本都完整保留，不删任何一行；
- 最高版本取值唯一且自身无字段问题 → 采纳最高版本，低版本记 `SUPERSEDED`；
- 最高版本出现两个不同取值 → 整组按 `DUP_DOC_CONFLICT` 隔离，谁都不信；
- 最高版本唯一但自身有字段问题（数量改坏 / 字段缺失等）→ 新版本按
  `REVISION_REJECTED` 隔离，再向低版本找第一条自身干净的记录回退采纳：
  回退行标 `valid` 并打 `FALLBACK_APPLIED` 原因与标记，让工作台看得见「单据已修订、
  新版被退回、实际仍按旧版入账」；找不到干净低版本才整组隔离，绝不静默沿用旧版；
- 同版本同值的重复行保留最先出现的一条，其余记 `DUPLICATE_ROW`。
"""
from __future__ import annotations

import hashlib
import json

from ... import reasons

# 行级标记：流水页、版本裁决页与证据 CSV 共用同一份字面量。
FALLBACK_FLAG = "FALLBACK_APPLIED"
REJECTED_FLAG = "REVISION_REJECTED"


def fingerprint(row: dict) -> str:
    payload = json.dumps(
        {"cells": sorted(row.get("cells", {}).items()), "raw": row.get("raw", [])},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _reason(row: dict) -> str:
    return reasons.primary(row["codes"]) or ""


def _clean_candidates(rows: list[dict]) -> list[dict]:
    """同版本同值时稳定取一条：指纹相同，按文件行号取最早，与输入顺序解耦。"""
    return sorted(rows, key=lambda row: (row.get("row_no", 0), row.get("seq", 0)))


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
        kept = _clean_candidates(contenders)[0]
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


def adjudicate(rows: list[dict]) -> list[dict]:
    """对带业务键的多版本行做版本裁决，返回逐键的裁决记录（见 revisions.py）。

    只处理 `(单据号, 行号)` 下多于一条记录的键；单条行的字段问题已在 build() 定级。
    每个键的结论只有四类：
    - revised        最高版本干净并被采纳，低版本被取代；
    - rejected_fallback 新版本判废，回退到某条干净低版本入账；
    - rejected_all   新版本判废且没有可回退的干净低版本，整组隔离；
    - conflict       最高版本同版本冲突，整组隔离。
    """
    from .revisions import build_decisions

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["doc_no"], row["line_no"]), []).append(row)

    decisions: list[dict] = []
    for key in sorted(groups):
        items = groups[key]
        if len(items) < 2:
            continue
        by_revision: dict[int, list[dict]] = {}
        for row in items:
            by_revision.setdefault(int(row["revision"]), []).append(row)
        revisions = sorted(by_revision, reverse=True)
        max_revision = revisions[0]
        latest = _clean_candidates(by_revision[max_revision])
        latest_distinct = {fingerprint(row) for row in latest}

        decision = {
            "doc_no": key[0],
            "line_no": key[1],
            "max_revision": max_revision,
            "revisions": revisions,
            "outcome": "",
            "applied_row_no": 0,
            "applied_revision": 0,
            "rejected_row_no": 0,
            "rejected_reason": "",
            "fallback_row_no": 0,
            "member_row_nos": sorted(row.get("row_no", 0) for row in items),
        }

        if len(latest_distinct) > 1:
            for row in items:
                row["status"] = "quarantine"
                row["reason"] = "DUP_DOC_CONFLICT"
                row["flags"] = ["REVISION_CONFLICT"]
            decision["outcome"] = "conflict"
            decision["rejected_row_no"] = latest[0].get("row_no", 0)
            decision["rejected_reason"] = "DUP_DOC_CONFLICT"
            decisions.append(decision)
            continue

        latest_kept = latest[0]
        if latest_kept["codes"]:
            # 新版本自身有字段问题：先隔离新版本（含其同版本重复行），再向旧版回退。
            reject_reason = reasons.primary(latest_kept["codes"]) or "REVISION_REJECTED"
            for row in by_revision[max_revision]:
                row["status"] = "quarantine"
                row["reason"] = "REVISION_REJECTED"
                row["flags"] = [REJECTED_FLAG, reject_reason]
            decision["rejected_row_no"] = latest_kept.get("row_no", 0)
            decision["rejected_reason"] = reject_reason

            fallback = None
            for revision in revisions[1:]:
                clean = [row for row in by_revision[revision] if not row["codes"]]
                if clean:
                    fallback = _clean_candidates(clean)[0]
                    break
            if fallback is None:
                # 没有干净的旧版可回退：整组隔离。脏行保留各自字段原因码，
                # 干净的旧版（理论上不会出现，防御性处理）记版本判废。
                for row in items:
                    if row["codes"]:
                        row["status"] = "quarantine"
                        row["reason"] = reasons.primary(row["codes"])
                        row["flags"] = []
                    else:
                        row["status"] = "quarantine"
                        row["reason"] = "REVISION_REJECTED"
                        row["flags"] = [REJECTED_FLAG]
                decision["outcome"] = "rejected_all"
                decisions.append(decision)
                continue

            fallback["status"] = "valid"
            fallback["reason"] = "FALLBACK_APPLIED"
            fallback["flags"] = [FALLBACK_FLAG, reject_reason]
            decision["outcome"] = "rejected_fallback"
            decision["fallback_row_no"] = fallback.get("row_no", 0)
            decision["applied_row_no"] = fallback.get("row_no", 0)
            decision["applied_revision"] = int(fallback["revision"])
            _mark_remaining(items, fallback, max_revision)
            decisions.append(decision)
            continue

        # 最高版本干净：正常的高版本覆盖，低版本被取代。
        latest_kept["status"] = "valid"
        latest_kept["reason"] = ""
        latest_kept["flags"] = []
        decision["outcome"] = "revised"
        decision["applied_row_no"] = latest_kept.get("row_no", 0)
        decision["applied_revision"] = max_revision
        _mark_remaining(items, latest_kept, max_revision)
        decisions.append(decision)

    return build_decisions(rows, decisions)


def _mark_remaining(items: list[dict], kept: dict, max_revision: int) -> None:
    """采纳行确定后，给同键其余行定重复 / 被取代状态。"""
    for row in items:
        if row is kept or row["status"] == "quarantine":
            continue
        if int(row["revision"]) == max_revision and not row["codes"]:
            row["status"] = "duplicate"
            row["reason"] = "DUPLICATE_ROW"
            row["flags"] = []
        else:
            row["status"] = "superseded"
            row["reason"] = "SUPERSEDED"
            row["flags"] = []


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

    # 先按「无字段问题」的行做常规去重，保证同版本同值的重复行口径不变；
    # 再处理多版本键里「新版本自身判废」的回退裁决。
    dedupe(rows)
    decisions = adjudicate(rows)

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
        "fallback": sum(
            1 for row in rows
            if row["status"] == "valid" and FALLBACK_FLAG in row.get("flags", [])),
    }
    return {"rows": rows, "counts": counts, "decisions": decisions}


def status_counts(rows: list[dict]) -> dict:
    counts = {"valid": 0, "quarantine": 0, "duplicate": 0, "superseded": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts
