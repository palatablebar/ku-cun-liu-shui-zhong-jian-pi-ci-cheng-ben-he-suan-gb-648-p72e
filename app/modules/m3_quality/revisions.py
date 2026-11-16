"""版本裁决视图：每张单据行最终采用哪一版，以及该选择对成本与期末库存的影响。

设计取舍（介于「只隔离新版本、旧数量继续静默入账」与「停用整张单据」之间）：
- 新版本判废时不拖累整张单据：同单据其他行照常重放，只有该业务键回退到干净旧版；
- 回退不是静默的：回退行打 `FALLBACK_APPLIED`，判废新版本打 `REVISION_REJECTED`，
  本模块逐键输出裁决结论与每个版本的行号、数量、单价、状态；
- 影响可量化：对「实际采纳版 ≠ 最新版」的键，用反事实重放（假设判废新版本被采纳）
  与真实重放对比，给出期末数量、期末金额、出库成本的差额，复核人一眼看见差异；
- 不丢记录：每个版本（含判废、被取代、重复）都在裁决记录与流水表里留痕；
- 确定性：裁决只看业务键、版本号、指纹与文件行号，反事实重放走同一条全序规则，
  同批输入重跑结论与差额完全一致。
"""
from __future__ import annotations

import copy
import csv
import io
from decimal import Decimal

from ... import config, reasons

# 裁决结论码 -> 中文名，页面与 CSV 同源。
OUTCOME_LABELS = {
    "revised": "采纳最新版",
    "rejected_fallback": "新版判废·回退旧版",
    "rejected_all": "新版判废·无可回退",
    "conflict": "同版本冲突·整组隔离",
}

HEADER = (
    "单据号", "单据行号", "裁决结论", "最高版本", "采纳版本", "采纳文件行",
    "判废文件行", "判废原因码", "判废原因", "回退文件行",
    "期末数量影响kg", "期末金额影响", "出库成本影响", "全部版本", "全部文件行",
)


def _dec(value) -> Decimal:
    return config.dec(value)


def _fmt(value: Decimal) -> str:
    return format(config.quantize_value(value), "f")


def _row_brief(row: dict) -> dict:
    return {
        "row_no": row.get("row_no", 0),
        "revision": int(row.get("revision") or 1),
        "status": row.get("status", ""),
        "reason": row.get("reason", ""),
        "qty_kg": format(config.quantize_qty(_dec(row.get("qty_kg"))), "f"),
        "price": format(config.quantize_cost(_dec(row.get("price"))), "f")
        if row.get("price") not in (None, "") else "",
        "raw_qty": row.get("raw_qty", ""),
        "raw_cost": row.get("raw_cost", ""),
    }


def build_decisions(rows: list[dict], base: list[dict]) -> list[dict]:
    """把 rules.adjudicate 的基础结论补全成可展示的逐键裁决记录。"""
    index: dict[tuple, list[dict]] = {}
    for row in rows:
        index.setdefault((row["doc_no"], row["line_no"]), []).append(row)

    decisions: list[dict] = []
    for item in base:
        key = (item["doc_no"], item["line_no"])
        members = sorted(index[key], key=lambda row: (int(row["revision"]),
                                                      row.get("row_no", 0)))
        item = dict(item)
        item["outcome_label"] = OUTCOME_LABELS.get(item["outcome"], item["outcome"])
        item["rejected_label"] = reasons.label(item["rejected_reason"]) \
            if item["rejected_reason"] else ""
        item["versions"] = [_row_brief(row) for row in members]
        item["impact"] = None
        decisions.append(item)
    return decisions


def _eligible_seqs(rows: list[dict], swap: dict | None) -> set[int]:
    """真实 / 反事实重放各自允许进入重放的行 seq 集合。"""
    seqs = {row["seq"] for row in rows if row["status"] == "valid"}
    if swap:
        seqs.discard(swap["remove_seq"])
        seqs.add(swap["add_seq"])
    return seqs


def _counterfactual_replay(rows: list[dict], opening_rows: list[dict],
                           settings, swap: dict) -> dict:
    """深拷贝后交换某一键的采纳行，用同一条 M4 全序规则重放。

    swap: {"remove_seq": 真实采纳行 seq, "add_seq": 反事实采纳行 seq}。
    只动这一个业务键的两行状态，其余行的状态、原因码原样保留。
    """
    from ..m4_replay import replay as replay_mod

    cloned = copy.deepcopy(rows)
    eligible = _eligible_seqs(cloned, swap)
    for row in cloned:
        if row["seq"] == swap["add_seq"]:
            row["status"] = "valid"
            row["reason"] = ""
            row["flags"] = []
        elif row["seq"] == swap["remove_seq"]:
            row["status"] = "quarantine"
            row["reason"] = "REVISION_REJECTED"
    opening_clone = copy.deepcopy(opening_rows)
    return replay_mod.replay(cloned, opening_clone, settings, eligible_seq=eligible)


def compute_impacts(decisions: list[dict], rows: list[dict],
                    opening_rows: list[dict], settings, actual_totals: dict) -> None:
    """对「实际采纳版不是最高版本」的键做反事实重放，就地写入 impact。

    impact 的符号统一为「反事实（若判废新版被采纳） − 实际（当前入账）」：
    正数表示一旦改用新版会增加该项，负数表示会减少。
    """
    actual_ending_qty = _dec(actual_totals["ending_qty"])
    actual_ending_value = _dec(actual_totals["ending_value"])
    actual_cogs = _dec(actual_totals["cogs"])

    for decision in decisions:
        if decision["outcome"] not in ("rejected_fallback", "conflict", "rejected_all"):
            continue
        latest_rows = [
            row for row in rows
            if (row["doc_no"], row["line_no"])
            == (decision["doc_no"], decision["line_no"])
            and int(row["revision"]) == decision["max_revision"]
        ]
        candidate = sorted(latest_rows, key=lambda row: row.get("row_no", 0))[0]

        # 新版本数量本身无法解析 / 缺失 / 非正时，反事实重放没有可信输入，
        # 不臆造数字：只保留裁决结论，不输出影响差额。
        if candidate.get("qty") is None or _dec(candidate.get("qty")) <= 0:
            decision["impact"] = None
            continue

        if decision["outcome"] == "rejected_fallback":
            applied = next(
                row for row in rows
                if row.get("row_no") == decision["applied_row_no"]
                and (row["doc_no"], row["line_no"])
                == (decision["doc_no"], decision["line_no"]))
            swap = {"remove_seq": applied["seq"], "add_seq": candidate["seq"]}
        else:
            # 整组隔离（冲突 / 无可回退）时没有真实采纳行：反事实即「假设文件行号
            # 最小的那条新版本可用」，只增不删；取行号最小保证结论确定、可复跑。
            swap = {"remove_seq": -1, "add_seq": candidate["seq"]}

        counter = _counterfactual_replay(rows, opening_rows, settings, swap)
        totals = counter["totals"]
        decision["impact"] = {
            "ending_qty_delta": _fmt(_dec(totals["ending_qty"]) - actual_ending_qty),
            "ending_value_delta": _fmt(
                _dec(totals["ending_value"]) - actual_ending_value),
            "cogs_delta": _fmt(_dec(totals["cogs"]) - actual_cogs),
            "counter_ending_qty": _fmt(totals["ending_qty"]),
            "counter_ending_value": _fmt(totals["ending_value"]),
            "counter_cogs": _fmt(totals["cogs"]),
        }


def csv_text(decisions: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for item in decisions:
        impact = item.get("impact") or {}
        writer.writerow(
            [
                item["doc_no"],
                item["line_no"],
                item["outcome_label"],
                item["max_revision"],
                item["applied_revision"],
                item["applied_row_no"],
                item["rejected_row_no"],
                item["rejected_reason"],
                item["rejected_label"],
                item["fallback_row_no"],
                impact.get("ending_qty_delta", ""),
                impact.get("ending_value_delta", ""),
                impact.get("cogs_delta", ""),
                "|".join(str(ver["revision"]) for ver in item["versions"]),
                "|".join(str(ver["row_no"]) for ver in item["versions"]),
            ]
        )
    return buffer.getvalue()


def summarize(decisions: list[dict]) -> dict:
    """控制台用的裁决分布与有影响的键数。"""
    counts: dict[str, int] = {}
    for item in decisions:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    impacted = [
        item for item in decisions
        if item.get("impact") and (
            _dec(item["impact"]["ending_qty_delta"]) != 0
            or _dec(item["impact"]["ending_value_delta"]) != 0
            or _dec(item["impact"]["cogs_delta"]) != 0)
    ]
    return {
        "total": len(decisions),
        "outcome_counts": [
            {
                "code": code,
                "label": OUTCOME_LABELS.get(code, code),
                "count": counts[code],
            }
            for code in sorted(counts)
        ],
        "impacted": len(impacted),
        "fallback": counts.get("rejected_fallback", 0),
        "rejected_all": counts.get("rejected_all", 0),
        "conflict": counts.get("conflict", 0),
    }
