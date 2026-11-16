"""应用装配：配置 → 存储 → 页面 → 七个功能模块路由。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import config, errors, pipeline, reasons, web
from .modules.m1_ingest import handler as m1_handler
from .modules.m2_doc_rules import handler as m2_handler
from .modules.m3_quality import handler as m3_handler
from .modules.m4_replay import handler as m4_handler
from .modules.m5_cost import handler as m5_handler
from .modules.m6_reconcile import handler as m6_handler
from .modules.m7_export import handler as m7_handler
from .store import Store

MODULES = (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler,
           m6_handler, m7_handler)


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()
    store = Store(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """容器首次启动时先跑一遍样例，页面打开就有批次。"""
        if settings.autoseed and store.latest_run() is None:
            pipeline.run_demo(settings, store)
        yield

    app = FastAPI(
        title="stockflow 库存流水重建与批次成本核算",
        description="采信 → 质量 → 重放 → 成本 → 盘点 → 导出",
        version=settings.rule_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    web.setup(app)
    app.mount("/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static")
    for module in MODULES:
        app.include_router(module.router)
    errors.install_handlers(app)

    @app.get("/api/health")
    def health(request: Request) -> dict:
        store = request.app.state.store
        run = store.latest_run()
        return {
            "status": "ok",
            "rule_version": settings.rule_version,
            "run_id": run["run_id"] if run else None,
            "status_of_run": run["status"] if run else "empty",
        }

    @app.post("/api/demo/run")
    def demo(request: Request) -> dict:
        return pipeline.run_demo(request.app.state.settings, request.app.state.store)

    @app.get("/api/log")
    def run_log(request: Request) -> dict:
        store = request.app.state.store
        run = store.latest_run()
        if not run:
            return {"run_id": None, "events": [], "count": 0}
        events = store.log_events(run["run_id"])
        return {"run_id": run["run_id"], "events": events, "count": len(events)}

    @app.get("/api/reasons")
    def reason_table() -> dict:
        return {
            "reasons": [
                {"code": code, "label": label, "stage": stage}
                for code, (label, stage) in reasons.REASONS.items()
            ],
            "priority": list(reasons.PRIORITY),
        }

    def _head_ok() -> Response:
        return Response(status_code=200, media_type="text/html; charset=utf-8")

    for path in ("/", "/movements", "/layers", "/cost", "/reconcile", "/export",
                 "/api/health"):
        app.add_api_route(path, _head_ok, methods=["HEAD"], include_in_schema=False)

    return app
