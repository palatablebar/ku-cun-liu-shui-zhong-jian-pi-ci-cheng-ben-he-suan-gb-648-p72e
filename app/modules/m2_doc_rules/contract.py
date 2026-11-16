"""单据规则：列名别名、单据类型方向、单价义务、单位与仓库登记。

这里是「定义层」的唯一来源：方向由单据类型决定，调拨再按方向列分成出边与入边；
单价义务按类型分档，入库没有单价就无法建层，必须拦下来。
"""
from __future__ import annotations

from ... import config

# 列名别名按文件种类分开登记，导入时先这里取，再交给采信层做表头识别。
ALIASES = {
    "movements": config.MOVEMENT_ALIASES,
    "opening": config.OPENING_ALIASES,
    "counts": config.COUNT_ALIASES,
}

DOC_TYPES = config.DOC_TYPES
DIRECTION_ALIASES = config.DIRECTION_ALIASES

DIRECTION_LABELS = {
    "IN": "入",
    "OUT": "出",
    "TRN_OUT": "调出",
    "TRN_IN": "调入",
    "ADJ_IN": "调整入",
    "ADJ_OUT": "调整出",
}


def aliases(kind: str) -> dict:
    return ALIASES[kind]


def doc_type_spec(doc_type: str) -> dict | None:
    return DOC_TYPES.get(doc_type)


def direction_for(doc_type: str, raw_direction: str) -> str | None:
    """返回 IN / OUT / TRN_OUT / TRN_IN / ADJ_IN / ADJ_OUT，未知返回 None。

    调拨与盘点调整看方向列；其余类型由类型固定，方向列不参与判定。
    """
    spec = DOC_TYPES.get(doc_type)
    if not spec:
        return None
    base = spec["dir"]
    if base == "IN":
        return "IN"
    if base == "OUT":
        return "OUT"
    if base == "RET":
        return "IN"
    mapped = DIRECTION_ALIASES.get(str(raw_direction or "").strip().lower())
    if base == "TRN":
        if mapped == "IN":
            return "TRN_IN"
        if mapped == "OUT":
            return "TRN_OUT"
        return None
    if base == "ADJ":
        if mapped == "IN":
            return "ADJ_IN"
        if mapped == "OUT":
            return "ADJ_OUT"
        return None
    return None


def price_required(doc_type: str, direction: str | None, ref_doc: str = "") -> bool:
    """该行是否必须带单价：入库类必填，调整入必填，无原单的退货必填。"""
    spec = DOC_TYPES.get(doc_type)
    if not spec:
        return False
    policy = spec["price"]
    if policy == "required":
        return True
    if policy == "conditional":
        return direction == "ADJ_IN"
    if policy == "optional":
        return not ref_doc
    return False


def describe(settings) -> dict:
    """GET /api/contract 的载荷：单据类型、方向、单价义务、单位换算与登记表。"""
    return {
        "rule_version": settings.rule_version,
        "timezone": settings.tz_name,
        "quantums": {
            "qty": format(settings.qty_quantum.normalize(), "f"),
            "cost": format(settings.cost_quantum.normalize(), "f"),
            "value": format(settings.value_quantum.normalize(), "f"),
        },
        "doc_types": [
            {
                "code": code,
                "direction": spec["dir"],
                "price": spec["price"],
                "price_required": price_required(code, "ADJ_IN"),
            }
            for code, spec in DOC_TYPES.items()
        ],
        "directions": DIRECTION_LABELS,
        "warehouses": [{"code": code, "name": name}
                       for code, name in settings.warehouses.items()],
        "skus": [{"code": code, "name": name} for code, name in settings.skus.items()],
        "units": [
            {"code": "kg", "factor": "1"},
            {"code": "g", "factor": "0.001"},
        ],
        "columns": {
            "movements": list(config.MOVEMENT_ALIASES),
            "opening": list(config.OPENING_ALIASES),
            "counts": list(config.COUNT_ALIASES),
        },
        "sentinels": list(settings.sentinels),
        "samples": [
            "movements_dirty.csv",
            "movements_clean.csv",
            "opening_balance.csv",
            "physical_counts.csv",
            "movements_truncated.csv",
            "movements_header_only.csv",
        ],
    }
