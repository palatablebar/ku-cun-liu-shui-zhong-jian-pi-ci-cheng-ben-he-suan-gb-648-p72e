"""出库成本：按单据归集，退货回原层冲减，分项与合计可复算。

净成本 = 消耗金额（出库/调拨出/调整出） − 退货回原层金额。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config

CONSUMING_KINDS = ("OUT", "TRN", "ADJ")


def by_doc(segments: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for seg in segments:
        key = seg.get("out_doc") or ""
        item = buckets.setdefault(
            key,
            {
                "doc_no": key,
                "kinds": [],
                "qty": Decimal("0"),
                "consumed": Decimal("0"),
                "returned": Decimal("0"),
                "segments": 0,
            },
        )
        if seg["kind"] not in item["kinds"]:
            item["kinds"].append(seg["kind"])
        item["segments"] += 1
        if seg["kind"] == "RET":
            item["returned"] += config.dec(seg["amount"])
        else:
            item["qty"] += config.dec(seg["qty"])
            item["consumed"] += config.dec(seg["amount"])
    out = []
    for item in sorted(buckets.values(), key=lambda row: row["doc_no"]):
        out.append(
            {
                "doc_no": item["doc_no"],
                "kinds": ",".join(sorted(item["kinds"])),
                "qty": format(config.quantize_qty(item["qty"]), "f"),
                "consumed": format(config.quantize_value(item["consumed"]), "f"),
                "returned": format(config.quantize_value(item["returned"]), "f"),
                "net": format(
                    config.quantize_value(item["consumed"] - item["returned"]), "f"),
                "segments": item["segments"],
            }
        )
    return out


def totals(segments: list[dict]) -> dict:
    consumed = sum((config.dec(seg["amount"]) for seg in segments
                    if seg["kind"] in CONSUMING_KINDS), Decimal("0"))
    restored = sum((config.dec(seg["amount"]) for seg in segments
                    if seg["kind"] == "RET"), Decimal("0"))
    qty = sum((config.dec(seg["qty"]) for seg in segments
               if seg["kind"] in CONSUMING_KINDS), Decimal("0"))
    return {
        "qty": format(config.quantize_qty(qty), "f"),
        "consumed": format(config.quantize_value(consumed), "f"),
        "restored": format(config.quantize_value(restored), "f"),
        "net": format(config.quantize_value(consumed - restored), "f"),
        "segments": len(segments),
    }


def by_kind(segments: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for seg in segments:
        item = buckets.setdefault(
            seg["kind"], {"kind": seg["kind"], "qty": Decimal("0"),
                          "amount": Decimal("0"), "segments": 0})
        item["qty"] += config.dec(seg["qty"])
        item["amount"] += config.dec(seg["amount"])
        item["segments"] += 1
    return [
        {
            "kind": item["kind"],
            "qty": format(config.quantize_qty(item["qty"]), "f"),
            "amount": format(config.quantize_value(item["amount"]), "f"),
            "segments": item["segments"],
        }
        for item in sorted(buckets.values(), key=lambda row: row["kind"])
    ]
