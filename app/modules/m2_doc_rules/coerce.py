"""字段归一：数字、整数、时间、单位、编码。

- 数字：NFKC 全角转半角、去千分位、去币种与单位后缀、括号负数，再 Decimal；
- 空值哨兵集中判定，返回 None 而不是 0；
- 时间：带偏移的先归一 UTC，裸时间按 Asia/Shanghai 解释，只给日期按当日 00:00；
- 编码：物料编码按字符串保留，不过 int()，前导零不丢。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ... import config
from ..m1_ingest.decode import normalize_cell

LOCAL_TZ = timezone(timedelta(hours=8))
CURRENCY_RE = re.compile(r"^(?:cny|rmb|[¥￥])", re.IGNORECASE)
SUFFIX_RE = re.compile(r"(?:kg|g|公斤|克|元|cn|人民币)$", re.IGNORECASE)


def is_sentinel(raw, sentinels=None) -> bool:
    text = normalize_cell(raw)
    table = config.SENTINELS if sentinels is None else sentinels
    return text.lower() in table


def parse_decimal(raw, sentinels=None) -> tuple[Decimal | None, str | None]:
    """返回 (值, 原因码)。哨兵返回 (None, 'SENTINEL')，解析失败返回 (None, 'PARSE')。"""
    text = normalize_cell(raw)
    table = config.SENTINELS if sentinels is None else sentinels
    if text.lower() in table:
        return None, "SENTINEL"
    cleaned = CURRENCY_RE.sub("", text).strip()
    cleaned = cleaned.replace(",", "").replace("\u00a0", "").replace(" ", "")
    cleaned = SUFFIX_RE.sub("", cleaned).strip()
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if cleaned.endswith("-"):
        negative = True
        cleaned = cleaned[:-1]
    if cleaned in ("", "-", "+"):
        return None, "SENTINEL"
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None, "PARSE"
    if not value.is_finite():
        return None, "PARSE"
    return (-value if negative else value), None


def parse_int(raw, sentinels=None) -> tuple[int | None, str | None]:
    value, code = parse_decimal(raw, sentinels)
    if value is None:
        return None, code
    if value != value.to_integral_value():
        return None, "PARSE"
    return int(value), None


def parse_instant(raw) -> tuple[str | None, str | None]:
    """返回 (UTC ISO 字符串, 原因码)。"""
    text = normalize_cell(raw)
    if not text:
        return None, "TS_MISSING"
    candidate = text.replace("/", "-")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    candidate = candidate.replace("T", " ")
    try:
        instant = datetime.fromisoformat(candidate)
    except ValueError:
        return None, "TS_PARSE"
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=LOCAL_TZ)
    utc = instant.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat(), None


def normalize_unit(raw, factors=None) -> tuple[str | None, Decimal | None, str | None]:
    """返回 (规范单位, 换算系数, 原因码)。规范单位只保留 kg 与 g。"""
    text = normalize_cell(raw)
    table = config.UNIT_FACTORS if factors is None else factors
    if not text:
        return None, None, "UNIT_MISSING"
    factor = table.get(text) or table.get(text.lower())
    if factor is None:
        return None, None, "UNIT_UNKNOWN"
    unit = "kg" if factor == 1 else "g"
    return unit, Decimal(factor), None


def to_kg(qty: Decimal | None, factor: Decimal | None) -> Decimal | None:
    if qty is None or factor is None:
        return None
    return config.quantize_qty(qty * factor)


def parse_sku(raw) -> str:
    """编码按字符串保形：去空格、去 Excel 前置单引号，不做数字转换。"""
    text = normalize_cell(raw)
    if text.startswith("'"):
        text = text[1:]
    return text
