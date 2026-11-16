"""原因码唯一来源。

隔离行、被取代行、重复行、盘点差异与对账提示都只从这里取码与中文名；
页面、CSV、运行清单共用同一份文字。
"""
from __future__ import annotations

# code -> (中文名, 所属阶段)
REASONS = {
    "EMPTY_FILE": ("空文件", "采信"),
    "HEADER_ONLY": ("仅表头", "采信"),
    "ALL_BLANK": ("全空行", "采信"),
    "HEADER_MISSING": ("表头缺失", "采信"),
    "TRUNCATED_TAIL": ("末行截断", "采信"),
    "COLUMN_COUNT": ("列数不齐", "采信"),
    "DOC_NO_MISSING": ("单据号缺失", "质量"),
    "LINE_NO_INVALID": ("行号无效", "质量"),
    "DOC_TYPE_UNKNOWN": ("单据类型未知", "质量"),
    "WAREHOUSE_UNKNOWN": ("仓库未登记", "质量"),
    "SKU_UNKNOWN": ("物料未登记", "质量"),
    "QTY_MISSING": ("数量为空值", "质量"),
    "QTY_PARSE": ("数量无法解析", "质量"),
    "QTY_NON_POSITIVE": ("数量非正", "质量"),
    "UNIT_MISSING": ("单位缺失", "质量"),
    "UNIT_UNKNOWN": ("单位不支持", "质量"),
    "TS_MISSING": ("发生时间缺失", "质量"),
    "TS_PARSE": ("发生时间无法解析", "质量"),
    "DIRECTION_UNKNOWN": ("方向未知", "质量"),
    "PRICE_REQUIRED": ("入库单价缺失", "质量"),
    "PRICE_PARSE": ("单价无法解析", "质量"),
    "REVISION_INVALID": ("版本号无效", "质量"),
    "DUP_DOC_CONFLICT": ("同版本取值冲突", "质量"),
    "DUPLICATE_ROW": ("完全重复行", "质量"),
    "SUPERSEDED": ("旧版本被取代", "质量"),
    "TRN_MISSING_LEG": ("调拨缺边", "重放"),
    "TRN_QTY_MISMATCH": ("调拨两边数量不等", "重放"),
    "TRN_SKU_MISMATCH": ("调拨两边物料不一致", "重放"),
    "TRN_SAME_WAREHOUSE": ("调拨两边同仓", "重放"),
    "TRN_LEG_AMBIGUOUS": ("调拨边数超限", "重放"),
    "TRN_LEG_BLOCKED": ("调拨对边被拦", "重放"),
    "OUT_EXCEEDS_STOCK": ("出库超过在手", "重放"),
    "ADJUST_EXCEEDS_STOCK": ("调整出超过在手", "重放"),
    "RETURN_DOC_NOT_FOUND": ("退货原单未找到", "重放"),
    "RETURN_WAREHOUSE_MISMATCH": ("退货仓库与原单不符", "重放"),
    "RETURN_EXCEEDS_ORIGINAL": ("退货超过原单消耗", "重放"),
    "COUNT_DIFF": ("盘点差异", "对账"),
    "COUNT_BALANCED": ("盘点相符", "对账"),
}

# 一行同时命中多条规则时，按此顺序取主原因码，保证结果不随检查顺序变化。
PRIORITY = (
    "TRUNCATED_TAIL",
    "COLUMN_COUNT",
    "DOC_NO_MISSING",
    "LINE_NO_INVALID",
    "REVISION_INVALID",
    "DOC_TYPE_UNKNOWN",
    "WAREHOUSE_UNKNOWN",
    "SKU_UNKNOWN",
    "TS_MISSING",
    "TS_PARSE",
    "QTY_MISSING",
    "QTY_PARSE",
    "QTY_NON_POSITIVE",
    "UNIT_MISSING",
    "UNIT_UNKNOWN",
    "DIRECTION_UNKNOWN",
    "PRICE_REQUIRED",
    "PRICE_PARSE",
    "TRN_MISSING_LEG",
    "TRN_QTY_MISMATCH",
    "TRN_SKU_MISMATCH",
    "TRN_SAME_WAREHOUSE",
    "TRN_LEG_AMBIGUOUS",
    "TRN_LEG_BLOCKED",
    "OUT_EXCEEDS_STOCK",
    "ADJUST_EXCEEDS_STOCK",
    "RETURN_DOC_NOT_FOUND",
    "RETURN_WAREHOUSE_MISMATCH",
    "RETURN_EXCEEDS_ORIGINAL",
    "DUP_DOC_CONFLICT",
)

STAGES = ("采信", "质量", "重放", "成本", "对账", "导出")


def label(code: str) -> str:
    return REASONS.get(code, ("未分类", "未知"))[0]


def stage_of(code: str) -> str:
    return REASONS.get(code, ("", "未知"))[1]


def primary(codes) -> str | None:
    """多条原因码取优先级最高的一条；都不在表里则取第一条。"""
    found = [c for c in PRIORITY if c in codes]
    if found:
        return found[0]
    return codes[0] if codes else None


def distribution(items) -> list[dict]:
    """按原因码统计条数，稳定排序。"""
    counts: dict[str, int] = {}
    for item in items:
        code = item.get("reason") or item.get("code") or "UNKNOWN"
        counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "label": label(code), "count": counts[code]}
        for code in sorted(counts)
    ]
