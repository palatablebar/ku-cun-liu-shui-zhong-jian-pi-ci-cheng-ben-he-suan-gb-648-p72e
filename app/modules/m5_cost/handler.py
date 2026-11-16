"""成本入口：FIFO 层、按单据出库成本与金额恒等式。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import service

router = APIRouter(prefix="/api/cost", tags=["FIFO 成本"])


@router.get("")
def get_cost(request: Request):
    store = request.app.state.store
    return service.payload(store, store.latest_run())


@router.get("/layers")
def get_layers(request: Request):
    store = request.app.state.store
    run = store.latest_run()
    if not run:
        return {"run_id": None, "rows": [], "count": 0}
    rows = store.layers(run["run_id"])
    return {"run_id": run["run_id"], "count": len(rows), "rows": rows}
