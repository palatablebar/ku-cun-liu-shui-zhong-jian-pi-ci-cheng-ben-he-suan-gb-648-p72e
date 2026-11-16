"""页面装配：模板环境、格式化过滤器、六个页面的上下文。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from . import reasons
from .config import BASE_DIR
from .modules.m5_cost import service as cost_service
from .modules.m6_reconcile import service as reconcile_service
from .modules.m7_export import service as export_service

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
LOCAL_TZ = timezone(timedelta(hours=8))

NAV = (
    ("控制台", "/"),
    ("流水", "/movements"),
    ("批次层", "/layers"),
    ("成本", "/cost"),
    ("盘点", "/reconcile"),
    ("导出", "/export"),
)

STATUS_LABELS = {
    "valid": "采纳",
    "quarantine": "隔离",
    "duplicate": "重复",
    "superseded": "被取代",
    "ready": "就绪",
    "empty": "空批次",
    "COUNT_DIFF": "差异",
    "COUNT_BALANCED": "相符",
    "COUNT_SHORT": "拒绝",
    "COUNT_INVALID": "无效",
}

DIRECTION_LABELS = {
    "IN": "入",
    "OUT": "出",
    "TRN_OUT": "调出",
    "TRN_IN": "调入",
    "ADJ_IN": "调整入",
    "ADJ_OUT": "调整出",
}

LAYER_ORIGINS = {
    "OPEN": "期初",
    "采购入库": "采购",
    "退货入库": "退货",
    "调拨": "调拨",
    "盘点": "盘点",
    "盘点调整": "调整",
}


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _trim(value: Decimal, min_dp: int) -> str:
    text = f"{value:,.4f}"
    integer, _, fraction = text.partition(".")
    while len(fraction) > min_dp and fraction.endswith("0"):
        fraction = fraction[:-1]
    if len(fraction) < min_dp:
        fraction = fraction.ljust(min_dp, "0")
    return f"{integer}.{fraction}"


def qty(value, min_dp: int = 2) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "0"
    return _trim(parsed, min_dp)


def money(value, min_dp: int = 2) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "0.00"
    return _trim(parsed, min_dp)


def local_dt(value) -> str:
    if not value:
        return "-"
    try:
        instant = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def log_dt(value) -> str:
    if not value:
        return "-"
    try:
        instant = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(LOCAL_TZ).strftime("%m-%d %H:%M")


def short_sha(value) -> str:
    return (str(value) or "")[:10]


def reason_label(code: str) -> str:
    return reasons.label(code)


def status_label(value) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, reasons.label(text) if text in reasons.REASONS else text)


def direction_label(value) -> str:
    return DIRECTION_LABELS.get(str(value or ""), str(value or ""))


def origin_label(value) -> str:
    return LAYER_ORIGINS.get(str(value or ""), str(value or ""))


def mark(ok) -> str:
    return "✓" if ok else "✕"


def layer_value(layer) -> str:
    return money(config_dec(layer.get("qty_remaining")) * config_dec(layer.get("unit_cost")))


def config_dec(value) -> Decimal:
    parsed = _decimal(value)
    return parsed if parsed is not None else Decimal("0")


def is_zero(value) -> bool:
    parsed = _decimal(value)
    return parsed is not None and parsed == 0


def setup(app: FastAPI) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters["qty"] = qty
    templates.env.filters["money"] = money
    templates.env.filters["localdt"] = local_dt
    templates.env.filters["shortsha"] = short_sha
    templates.env.filters["logdt"] = log_dt
    templates.env.filters["reason_label"] = reason_label
    templates.env.filters["status_label"] = status_label
    templates.env.filters["direction_label"] = direction_label
    templates.env.filters["origin_label"] = origin_label
    templates.env.filters["mark"] = mark
    templates.env.filters["layervalue"] = layer_value
    templates.env.filters["zerorem"] = is_zero
    app.state.templates = templates

    def base(request: Request, title: str, current: str) -> dict:
        store = request.app.state.store
        return {
            "request": request,
            "title": title,
            "current": current,
            "nav": NAV,
            "run": store.latest_run(),
            "settings": request.app.state.settings,
            "runs": store.list_runs(6),
        }

    def render(request: Request, template: str, context: dict):
        return templates.TemplateResponse(request, template, context)

    @app.get("/")
    def index(request: Request):
        store = request.app.state.store
        context = base(request, "控制台", "/")
        run = context["run"]
        cost = cost_service.payload(store, run)
        report = reconcile_service.payload(store, run)
        movements = store.movements(run["run_id"]) if run else []
        context.update(
            {
                "cost": cost,
                "report": report,
                "log": store.log_events(run["run_id"]) if run else [],
                "quarantine": [row for row in movements if row["status"] == "quarantine"],
                "distribution": reasons.distribution(
                    [row for row in movements if row["status"] == "quarantine"]),
                "status_counts": {
                    "valid": sum(1 for row in movements if row["status"] == "valid"),
                    "quarantine": sum(
                        1 for row in movements if row["status"] == "quarantine"),
                    "superseded": sum(
                        1 for row in movements if row["status"] == "superseded"),
                    "duplicate": sum(
                        1 for row in movements if row["status"] == "duplicate"),
                },
            }
        )
        return render(request, "index.html", context)

    @app.get("/movements")
    def movements_page(request: Request, status: str = "", reason: str = ""):
        store = request.app.state.store
        context = base(request, "流水", "/movements")
        run = context["run"]
        rows = store.movements(run["run_id"]) if run else []
        distribution = reasons.distribution(
            [row for row in rows if row["status"] == "quarantine"])
        filtered = [
            row for row in rows
            if (not status or row["status"] == status)
            and (not reason or row["reason"] == reason)
        ]
        context.update(
            {
                "rows": filtered[:400],
                "total": len(filtered),
                "truncated": len(filtered) > 400,
                "status": status,
                "reason": reason,
                "distribution": distribution,
                "statuses": ["valid", "quarantine", "superseded", "duplicate"],
            }
        )
        return render(request, "movements.html", context)

    @app.get("/layers")
    def layers_page(request: Request):
        store = request.app.state.store
        context = base(request, "批次层", "/layers")
        run = context["run"]
        cost = cost_service.payload(store, run)
        context.update(
            {
                "layers": cost["layers"],
                "layer_rows": cost["layer_rows"],
                "layer_totals": cost["layer_totals"],
                "checks": cost["checks"],
            }
        )
        return render(request, "layers.html", context)

    @app.get("/cost")
    def cost_page(request: Request):
        store = request.app.state.store
        context = base(request, "成本", "/cost")
        run = context["run"]
        context.update({"cost": cost_service.payload(store, run)})
        return render(request, "cost.html", context)

    @app.get("/reconcile")
    def reconcile_page(request: Request):
        store = request.app.state.store
        context = base(request, "盘点", "/reconcile")
        run = context["run"]
        report = reconcile_service.payload(store, run)
        context.update(
            {
                "report": report,
                "counts": report.get("counts", []),
                "checks": report.get("checks", []),
                "totals": report.get("totals", {}),
                "adjust": report.get("adjust", {}),
            }
        )
        return render(request, "reconcile.html", context)

    @app.get("/export")
    def export_page(request: Request):
        store = request.app.state.store
        context = base(request, "导出", "/export")
        run = context["run"]
        context.update(
            {
                "summary": export_service.summary(store, run),
                "manifest": _manifest_preview(store, run),
            }
        )
        return render(request, "export.html", context)


def _manifest_preview(store, run) -> list[str]:
    if not run:
        return []
    from .modules.m6_reconcile import balance as balance_mod
    from .modules.m7_export import manifest_out

    report = balance_mod.reconcile(
        run, store.movements(run["run_id"]), store.layers(run["run_id"]),
        store.consumptions(run["run_id"]), store.counts(run["run_id"]),
    )
    return manifest_out.manifest_json(run, report["checks"]).splitlines()[:26]
