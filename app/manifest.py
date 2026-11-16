"""运行清单：run_id、输入摘要、规则版本、参数与计数。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def make_run_id(movements_sha: str, opening_sha: str, counts_sha: str,
                rule_version: str, params: dict) -> str:
    """同一组输入 + 同一组参数得到同一个 run_id，重跑命中同一批次。"""
    payload = "|".join(
        [
            rule_version,
            movements_sha or "-",
            opening_sha or "-",
            counts_sha or "-",
            ",".join(f"{key}={params[key]}" for key in sorted(params)),
        ]
    )
    return "run-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_safe(value):
    """Decimal / datetime 转字符串，大整数与编码保持字符串。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def build_manifest(run: dict, checks: list[dict] | None = None) -> dict:
    return {
        "run_id": run["run_id"],
        "created_at": run["created_at"],
        "rule_version": run["rule_version"],
        "params": run.get("params", {}),
        "inputs": {
            "movements": {
                "file": run.get("movements_file") or "",
                "sha256": run.get("movements_sha256") or "",
                "bytes": run.get("movements_bytes", 0),
            },
            "opening": {
                "file": run.get("opening_file") or "",
                "sha256": run.get("opening_sha256") or "",
                "bytes": run.get("opening_bytes", 0),
            },
            "counts": {
                "file": run.get("counts_file") or "",
                "sha256": run.get("counts_sha256") or "",
                "bytes": run.get("counts_bytes", 0),
            },
        },
        "counts": {
            "raw_rows": run.get("raw_rows", 0),
            "applied": run.get("applied", 0),
            "quarantined": run.get("quarantined", 0),
            "superseded": run.get("superseded", 0),
            "duplicate": run.get("duplicate", 0),
            "blank": run.get("blank", 0),
            "segments": run.get("segments", 0),
        },
        "quantities": {
            "opening": run.get("opening_qty", "0"),
            "in": run.get("in_qty", "0"),
            "out": run.get("out_qty", "0"),
            "adj_in": run.get("adj_in_qty", "0"),
            "adj_out": run.get("adj_out_qty", "0"),
            "ending": run.get("ending_qty", "0"),
        },
        "cost": {
            "cogs": run.get("cogs", "0"),
            "ending_value": run.get("ending_value", "0"),
        },
        "checks": checks or [],
        "duration_ms": run.get("duration_ms", 0),
        "status": run.get("status", ""),
    }
