"""时序重放：调拨双边配对 → 全序重放 → FIFO 消耗 / 建层 / 退货回层。

规则（朴素实现最容易做错的地方）：
- 全序按 (发生时间, 单据号, 方向序, 行号, 物料, 仓库) 排，文件里的乱序不影响结果；
- 出库超过在手时整行隔离，绝不压成负库存，后续行照常重放；
- 调拨必须一条出边一条入边、同物料、异仓、数量相等，缺边与不等都整组隔离；
- 入边层成本 = 出边实际消耗金额 / 数量，量化到四位；
- 退货带原单号才回原层（按原消耗顺序），无原单按本次单价建新层。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from . import fifo

DIRECTION_RANK = {"TRN_OUT": 0, "TRN_IN": 1}


def sort_key(row: dict) -> tuple:
    return (
        row.get("ts_utc") or "",
        row.get("doc_no") or "",
        DIRECTION_RANK.get(row.get("direction") or "", 0),
        int(row.get("line_no") or 0),
        row.get("sku") or "",
        row.get("warehouse") or "",
    )


def pair_transfers(trn_rows: list[dict]) -> tuple[dict, dict]:
    """按单据号把调拨的两条边配成一对；配不上的组返回隔离原因。"""
    groups: dict[str, list[dict]] = {}
    for row in trn_rows:
        groups.setdefault(row["doc_no"], []).append(row)

    pairs: dict[str, tuple[dict, dict]] = {}
    broken: dict[str, tuple[str, list[dict]]] = {}
    for doc_no, legs in groups.items():
        if len(legs) != 2:
            broken[doc_no] = (
                "TRN_LEG_AMBIGUOUS" if len(legs) > 2 else "TRN_MISSING_LEG", legs)
            continue
        outs = [leg for leg in legs if leg["direction"] == "TRN_OUT"]
        ins = [leg for leg in legs if leg["direction"] == "TRN_IN"]
        if len(outs) != 1 or len(ins) != 1:
            broken[doc_no] = ("TRN_MISSING_LEG", legs)
            continue
        out_leg, in_leg = outs[0], ins[0]
        if out_leg["sku"] != in_leg["sku"]:
            broken[doc_no] = ("TRN_SKU_MISMATCH", legs)
            continue
        if out_leg["warehouse"] == in_leg["warehouse"]:
            broken[doc_no] = ("TRN_SAME_WAREHOUSE", legs)
            continue
        if config.dec(out_leg["qty_kg"]) != config.dec(in_leg["qty_kg"]):
            broken[doc_no] = ("TRN_QTY_MISMATCH", legs)
            continue
        pairs[doc_no] = (out_leg, in_leg)
    return pairs, broken


def opening_layers(opening_rows: list[dict]) -> list[dict]:
    layers: list[dict] = []
    for row in opening_rows:
        if row.get("status") != "valid":
            continue
        qty = config.dec(row.get("qty_kg"))
        if qty <= 0:
            continue
        fifo.new_layer(
            layers, row["sku"], row["warehouse"], "OPEN", "", 0, "",
            config.dec(row.get("price")), qty,
        )
    return layers


def replay(rows: list[dict], opening_rows: list[dict], settings) -> dict:
    """返回层、消耗段、逐行状态与四本账的数量与金额分项。"""
    for row in rows:
        row["flags"] = list(row.get("flags") or [])
        row["applied_seq"] = 0

    valid = [row for row in rows if row["status"] == "valid"]
    pairs, broken = pair_transfers(
        [row for row in valid if row["direction"] in ("TRN_OUT", "TRN_IN")]
    )
    for reason, legs in broken.values():
        for leg in legs:
            leg["status"] = "quarantine"
            leg["reason"] = reason
            leg["flags"].append("TRN_GROUP")

    layers = opening_layers(opening_rows)
    layers_by_id = {layer["layer_id"]: layer for layer in layers}
    by_key: dict[tuple, list[dict]] = {}
    for layer in layers:
        by_key.setdefault(fifo.key_of(layer["sku"], layer["warehouse"]), []).append(layer)
    opening_qty = sum((config.dec(layer["qty_in"]) for layer in layers), Decimal("0"))
    opening_value = sum((fifo.layer_value(layer) for layer in layers), Decimal("0"))

    segments: list[dict] = []
    seg_counter = [1]
    returnable: dict[str, Decimal] = {}
    consumed_by_doc: dict[str, list[dict]] = {}
    pair_done: set[str] = set()
    pair_blocked: set[str] = set()
    totals = {
        "opening_qty": opening_qty,
        "opening_value": opening_value,
        "in_qty": Decimal("0"),
        "in_value": Decimal("0"),
        "out_qty": Decimal("0"),
        "out_value": Decimal("0"),
        "adj_in_qty": Decimal("0"),
        "adj_in_value": Decimal("0"),
        "adj_out_qty": Decimal("0"),
        "adj_out_value": Decimal("0"),
        "return_qty": Decimal("0"),
        "return_value": Decimal("0"),
        "trn_in_qty": Decimal("0"),
        "trn_in_value": Decimal("0"),
        "trn_out_qty": Decimal("0"),
        "trn_out_value": Decimal("0"),
    }

    def fail(row: dict, reason: str) -> None:
        row["status"] = "quarantine"
        row["reason"] = reason
        row["applied_seq"] = 0

    def register_layer(layer: dict) -> None:
        layers_by_id[layer["layer_id"]] = layer
        by_key.setdefault(fifo.key_of(layer["sku"], layer["warehouse"]), []).append(layer)

    def consume_into(row: dict, direction: str, qty: Decimal):
        bucket = by_key.setdefault(fifo.key_of(row["sku"], row["warehouse"]), [])
        produced = fifo.consume(bucket, qty, "S", seg_counter)
        if produced is None:
            return None, Decimal("0")
        amount = sum((seg["amount"] for seg in produced), Decimal("0"))
        kind = {"OUT": "OUT", "TRN_OUT": "TRN", "ADJ_OUT": "ADJ"}[direction]
        for seg in produced:
            seg.update(
                {
                    "out_doc": row["doc_no"],
                    "out_line": row["line_no"],
                    "sku": row["sku"],
                    "warehouse": row["warehouse"],
                    "kind": kind,
                }
            )
            returnable[seg["seg_id"]] = seg["qty"]
        segments.extend(produced)
        consumed_by_doc.setdefault(row["doc_no"], []).extend(produced)
        return produced, amount

    applied = 0
    for row in sorted(valid, key=sort_key):
        if row["status"] != "valid":
            continue
        direction = row["direction"]
        qty = config.dec(row["qty_kg"])

        if direction == "IN":
            ref = row.get("ref_doc") or ""
            if ref:
                original = consumed_by_doc.get(ref)
                if not original:
                    fail(row, "RETURN_DOC_NOT_FOUND")
                    continue
                if original[0]["warehouse"] != row["warehouse"]:
                    fail(row, "RETURN_WAREHOUSE_MISMATCH")
                    continue
                plan = fifo.plan_restore(original, returnable, qty)
                if plan is None:
                    fail(row, "RETURN_EXCEEDS_ORIGINAL")
                    continue
                restored = fifo.apply_restore(plan, layers_by_id, returnable,
                                              "R", seg_counter)
                for seg in restored:
                    seg.update(
                        {
                            "out_doc": row["doc_no"],
                            "out_line": row["line_no"],
                            "sku": row["sku"],
                            "warehouse": row["warehouse"],
                            "kind": "RET",
                        }
                    )
                segments.extend(restored)
                restored_value = sum((seg["amount"] for seg in restored), Decimal("0"))
                # 回原层不是新建层：只进数量与退货金额，不进建层金额。
                totals["in_qty"] += qty
                totals["return_qty"] += qty
                totals["return_value"] += restored_value
            else:
                price = config.dec(row.get("price"))
                layer = fifo.new_layer(
                    layers, row["sku"], row["warehouse"], row["doc_type"], row["doc_no"],
                    row["line_no"], row["ts_utc"], price, qty,
                )
                register_layer(layer)
                totals["in_qty"] += qty
                totals["in_value"] += qty * config.dec(layer["unit_cost"])
                if row["doc_type"] == "退货入库":
                    totals["return_qty"] += qty
                    totals["return_value"] += qty * config.dec(layer["unit_cost"])

        elif direction == "ADJ_IN":
            price = config.dec(row.get("price"))
            layer = fifo.new_layer(
                layers, row["sku"], row["warehouse"], row["doc_type"], row["doc_no"],
                row["line_no"], row["ts_utc"], price, qty,
            )
            register_layer(layer)
            totals["adj_in_qty"] += qty
            totals["adj_in_value"] += qty * config.dec(layer["unit_cost"])

        elif direction == "OUT":
            produced, amount = consume_into(row, direction, qty)
            if produced is None:
                fail(row, "OUT_EXCEEDS_STOCK")
                continue
            totals["out_qty"] += qty
            totals["out_value"] += amount

        elif direction == "ADJ_OUT":
            produced, amount = consume_into(row, direction, qty)
            if produced is None:
                fail(row, "ADJUST_EXCEEDS_STOCK")
                continue
            totals["adj_out_qty"] += qty
            totals["adj_out_value"] += amount

        elif direction == "TRN_OUT":
            produced, amount = consume_into(row, direction, qty)
            if produced is None:
                fail(row, "OUT_EXCEEDS_STOCK")
                pair_blocked.add(row["doc_no"])
                continue
            totals["out_qty"] += qty
            totals["trn_out_qty"] += qty
            totals["trn_out_value"] += amount
            pair_done.add(row["doc_no"])
            unit_cost = ((amount / qty).quantize(config.COST_QUANTUM)
                         if qty else Decimal("0"))
            in_leg = pairs[row["doc_no"]][1]
            layer = fifo.new_layer(
                layers, in_leg["sku"], in_leg["warehouse"], "调拨", row["doc_no"],
                in_leg["line_no"], in_leg["ts_utc"], unit_cost, qty,
            )
            register_layer(layer)
            totals["in_qty"] += qty
            totals["trn_in_qty"] += qty
            totals["trn_in_value"] += qty * config.dec(layer["unit_cost"])

        elif direction == "TRN_IN":
            if row["doc_no"] in pair_done:
                applied += 1
                row["applied_seq"] = applied
                row["flags"].append("APPLIED")
                continue
            fail(row, "TRN_LEG_BLOCKED" if row["doc_no"] in pair_blocked
                 else "TRN_MISSING_LEG")
            continue

        applied += 1
        row["applied_seq"] = applied
        row["flags"].append("APPLIED")

    consumed_value = sum((seg["amount"] for seg in segments if seg["kind"] != "RET"),
                         Decimal("0"))
    restored_value = sum((seg["amount"] for seg in segments if seg["kind"] == "RET"),
                         Decimal("0"))
    totals.update(
        {
            "ending_qty": sum((config.dec(layer["qty_remaining"]) for layer in layers),
                              Decimal("0")),
            "ending_value": sum((fifo.layer_value(layer) for layer in layers),
                                Decimal("0")),
            "consumed_value": consumed_value,
            "restored_value": restored_value,
            "cogs": consumed_value - restored_value,
            "applied": applied,
        }
    )
    return {
        "layers": layers,
        "segments": segments,
        "rows": rows,
        "totals": totals,
        "broken_transfers": {doc: reason for doc, (reason, _) in broken.items()},
        "pairs": len(pairs),
    }
