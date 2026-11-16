"""流式读入一份导出文件：解码 → CSV 解析 → 表头识别 → 分段与列校验。

用 csv 模块而不是 splitlines()，单元格里的换行与引号不会被切成两条记录；
重复表头（分段导出）计入段数但不作为数据行；末行缺列且文件不以换行结束时判截断。
"""
from __future__ import annotations

import csv
import io

from .decode import alias_token, decode_bytes, normalize_cell


def header_like(cells, tokens) -> bool:
    hits = 0
    for cell in cells:
        token = alias_token(cell)
        if token and token in tokens:
            hits += 1
    return hits >= 3


def read_table(raw: bytes, aliases: dict, encodings) -> dict:
    """aliases: 内部列名 -> 该列可接受的原始列名元组。"""
    decoded = decode_bytes(raw, encodings)
    if decoded["error"] == "EMPTY_FILE":
        return {
            "state": "EMPTY_FILE",
            "encoding": None,
            "bom": False,
            "segments": 0,
            "blank_rows": 0,
            "records": [],
            "raw_rows": 0,
            "header": [],
        }

    token_index: dict[str, str] = {}
    for canonical, names in aliases.items():
        for name in names:
            token_index[alias_token(name)] = canonical
    tokens = set(token_index)

    text = decoded["text"]
    trailing_newline = text.endswith("\n")

    rows: list[tuple[int, list[str]]] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    previous_end = 0
    for cells in reader:
        start = previous_end + 1
        previous_end = reader.line_num
        rows.append((start, cells))

    header: list[str] | None = None
    header_map: dict[str, int] = {}
    segments = 0
    blank_rows = 0
    records: list[dict] = []

    for row_no, cells in rows:
        normalized = [normalize_cell(cell) for cell in cells]
        if not any(normalized):
            blank_rows += 1
            continue
        if header_like(normalized, tokens):
            segments += 1
            if header is None:
                header = normalized
                for index, name in enumerate(normalized):
                    canonical = token_index.get(alias_token(name))
                    if canonical and canonical not in header_map:
                        header_map[canonical] = index
            continue
        if header is None:
            return {
                "state": "HEADER_MISSING",
                "encoding": decoded["encoding"],
                "bom": decoded["bom"],
                "segments": segments,
                "blank_rows": blank_rows,
                "records": [],
                "raw_rows": 0,
                "header": [],
            }
        cells_map = {
            canonical: (cells[index] if index < len(cells) else "")
            for canonical, index in header_map.items()
        }
        records.append(
            {
                "row_no": row_no,
                "cells": cells_map,
                "raw": cells,
                "column_ok": len(cells) == len(header),
                "truncated": False,
            }
        )

    if records and not trailing_newline:
        last = records[-1]
        if not last["column_ok"]:
            last["truncated"] = True

    state = None
    if header is None:
        state = "HEADER_MISSING"
    elif not records:
        state = "ALL_BLANK" if blank_rows else "HEADER_ONLY"

    return {
        "state": state,
        "encoding": decoded["encoding"],
        "bom": decoded["bom"],
        "segments": segments,
        "blank_rows": blank_rows,
        "records": records,
        "raw_rows": len(records),
        "header": header or [],
        "header_map": header_map,
    }
