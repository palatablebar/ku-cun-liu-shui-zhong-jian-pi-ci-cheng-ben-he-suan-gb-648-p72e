"""运行参数与常量：仓库登记、物料主数据、单据类型、单位换算、编码链、空值哨兵、量化刻度。

阈值与登记表只在这里定义一次：规则模块、页面、导出与测试都从这里取，
避免同一份定义在两处漂移。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

RULE_VERSION = "2026.11.1"
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

# 编码回退链：先按带 BOM 的 UTF-8 严格解，再 UTF-8，最后 GB18030。
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")

# 空值哨兵：命中表示「没有填」，不是 0；数量、单价、实盘数量都走这张表。
SENTINELS = ("", "-", "--", "/", "n/a", "na", "null", "none", "未知")

# 三个区域仓；未登记的仓库不进重放。
WAREHOUSES = {
    "WH-A": "东区仓",
    "WH-B": "南区仓",
    "WH-C": "北区仓",
}

# 物料主数据：编码一律字符串，前导零不丢。
SKUS = {
    "000123": "冷鲜鸡胸",
    "000456": "冷冻牛腩",
    "000789": "冰鲜三文鱼",
    "000999": "熟制鸭腿",
}

# 单位换算：数量先折成 kg，成本单价一律按 kg 记。
UNIT_FACTORS = {
    "kg": Decimal("1"),
    "公斤": Decimal("1"),
    "g": Decimal("0.001"),
    "克": Decimal("0.001"),
}

# 单据类型：方向与单价义务。
# dir: IN/OUT/RET/ADJ/TRN；price: required / ignored / optional / conditional（仅调整入需要）
DOC_TYPES = {
    "采购入库": {"dir": "IN", "price": "required"},
    "销售出库": {"dir": "OUT", "price": "ignored"},
    "生产领料": {"dir": "OUT", "price": "ignored"},
    "退货入库": {"dir": "RET", "price": "optional"},
    "调拨": {"dir": "TRN", "price": "ignored"},
    "盘点调整": {"dir": "ADJ", "price": "conditional"},
}

DIRECTION_ALIASES = {
    "入": "IN", "进": "IN", "入库": "IN", "in": "IN", "+": "IN",
    "出": "OUT", "出库": "OUT", "out": "OUT", "-": "OUT",
}

QTY_QUANTUM = Decimal("0.0001")
COST_QUANTUM = Decimal("0.0001")
VALUE_QUANTUM = Decimal("0.0001")

# 列别名：同一列在不同系统导出里的写法。键是内部列名。
MOVEMENT_ALIASES = {
    "doc_no": ("单据号", "单据编号", "单号", "doc_no", "docno"),
    "line_no": ("行号", "行", "line_no", "line"),
    "doc_type": ("类型", "单据类型", "doc_type", "type"),
    "warehouse": ("仓库", "仓", "warehouse", "wh"),
    "sku": ("物料编码", "物料号", "物料", "商品编码", "sku"),
    "qty": ("数量", "qty", "quantity"),
    "unit": ("单位", "unit", "uom"),
    "price": ("单价", "price", "unit_price"),
    "ts": ("发生时间", "时间", "ts", "occurred_at"),
    "direction": ("方向", "dir", "direction"),
    "ref_doc": ("关联单号", "原单号", "ref_doc", "ref"),
    "revision": ("版本", "revision", "rev", "ver"),
}

OPENING_ALIASES = {
    "warehouse": ("仓库", "仓", "warehouse", "wh"),
    "sku": ("物料编码", "物料号", "物料", "商品编码", "sku"),
    "qty": ("数量", "期初数量", "qty", "quantity"),
    "unit": ("单位", "unit", "uom"),
    "price": ("单价", "期初单价", "price", "unit_cost"),
}

COUNT_ALIASES = {
    "count_no": ("盘点单号", "盘点号", "count_no"),
    "warehouse": ("仓库", "仓", "warehouse", "wh"),
    "sku": ("物料编码", "物料号", "物料", "商品编码", "sku"),
    "qty": ("实盘数量", "盘点数量", "qty", "counted_qty"),
    "unit": ("单位", "unit", "uom"),
    "revision": ("版本", "revision", "rev"),
}


def dec(value) -> Decimal:
    """把存储里的字符串转回 Decimal；已是 Decimal 时原样返回。"""
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def quantize_qty(value) -> Decimal:
    return dec(value).quantize(QTY_QUANTUM)


def quantize_cost(value) -> Decimal:
    return dec(value).quantize(COST_QUANTUM)


def quantize_value(value) -> Decimal:
    return dec(value).quantize(VALUE_QUANTUM)


def registry_digest() -> str:
    """仓库 + 物料 + 单据类型 + 单位换算的摘要，进入 run_id。"""
    payload = json.dumps(
        {
            "warehouses": sorted(WAREHOUSES.items()),
            "skus": sorted(SKUS.items()),
            "doc_types": {k: dict(v) for k, v in sorted(DOC_TYPES.items())},
            "units": {k: format(v.normalize(), "f") for k, v in sorted(UNIT_FACTORS.items())},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Settings:
    db_path: str
    rule_version: str = RULE_VERSION
    tz_name: str = "Asia/Shanghai"
    sample_dir: str = str(PROJECT_DIR / "sample_data")
    encodings: tuple = ENCODING_CHAIN
    sentinels: tuple = SENTINELS
    warehouses: dict = field(default_factory=lambda: dict(WAREHOUSES))
    skus: dict = field(default_factory=lambda: dict(SKUS))
    unit_factors: dict = field(default_factory=lambda: dict(UNIT_FACTORS))
    qty_quantum: Decimal = QTY_QUANTUM
    cost_quantum: Decimal = COST_QUANTUM
    value_quantum: Decimal = VALUE_QUANTUM
    autoseed: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get(
                "STOCKFLOW_DB", str(PROJECT_DIR / "var" / "stockflow.sqlite")
            ),
            rule_version=os.environ.get("RULE_VERSION", RULE_VERSION),
            sample_dir=os.environ.get("SAMPLE_DIR", str(PROJECT_DIR / "sample_data")),
            tz_name=os.environ.get("TZ", "Asia/Shanghai"),
            autoseed=os.environ.get("STOCKFLOW_AUTOSEED", "0") == "1",
        )

    def params(self) -> dict:
        return {
            "qty_quantum": format(self.qty_quantum.normalize(), "f"),
            "cost_quantum": format(self.cost_quantum.normalize(), "f"),
            "value_quantum": format(self.value_quantum.normalize(), "f"),
            "timezone": self.tz_name,
            "registry": registry_digest(),
        }
