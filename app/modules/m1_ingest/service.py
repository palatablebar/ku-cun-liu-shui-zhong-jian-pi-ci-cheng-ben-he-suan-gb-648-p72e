"""采信编排：读取样例或上传文件，返回逐行诊断与文件摘要。

本模块只负责「读进来、切行、判列」；列名映射由单据规则模块提供，字段归一交给契约模块。
"""
from __future__ import annotations

from pathlib import Path

from ...errors import DomainError
from ...manifest import sha256_hex
from .reader import read_table


def resolve_sample(settings, name: str) -> tuple[str, bytes]:
    base = Path(settings.sample_dir).resolve()
    path = (base / name).resolve()
    if base not in path.parents and path.parent != base:
        raise DomainError("E_SAMPLE_PATH", "样例文件名不合法", 400)
    if not path.is_file():
        raise DomainError("E_SAMPLE_NOT_FOUND", f"样例不存在：{name}", 404)
    return name, path.read_bytes()


def read_file(settings, filename: str, raw: bytes, aliases: dict) -> dict:
    """读一份文件并补齐摘要字段；空文件、仅表头、全空行都走同一条路径。"""
    parsed = read_table(raw, aliases, settings.encodings)
    parsed["filename"] = filename
    parsed["bytes"] = len(raw)
    parsed["sha256"] = sha256_hex(raw)
    return parsed
