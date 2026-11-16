"""运行清单导出：run_id、三份输入摘要、参数、计数与对账结论。"""
from __future__ import annotations

import json

from ...manifest import build_manifest


def manifest_json(run: dict, checks: list[dict]) -> str:
    payload = build_manifest(run, checks)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
