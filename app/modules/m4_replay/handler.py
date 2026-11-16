"""重放入口：单独跑一遍时序重放，返回层数、消耗段与数量分项。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import service

router = APIRouter(prefix="/api/replay", tags=["时序重放"])


@router.post("")
def run_replay(request: Request):
    settings = request.app.state.settings
    store = request.app.state.store
    return service.apply(settings, store, store.latest_run())


@router.get("")
def replay_state(request: Request):
    store = request.app.state.store
    run = store.latest_run()
    if not run:
        return {"run_id": None, "layers": 0, "segments": 0}
    return {
        "run_id": run["run_id"],
        "layers": len(store.layers(run["run_id"])),
        "segments": len(store.consumptions(run["run_id"])),
        "quantities": {
            "opening": run["opening_qty"],
            "in": run["in_qty"],
            "out": run["out_qty"],
            "adj_in": run["adj_in_qty"],
            "adj_out": run["adj_out_qty"],
            "ending": run["ending_qty"],
        },
        "cogs": run["cogs"],
        "ending_value": run["ending_value"],
    }
