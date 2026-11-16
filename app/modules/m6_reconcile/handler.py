"""盘点入口：对账六本账、提交实盘。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from . import service

router = APIRouter(prefix="/api/reconcile", tags=["盘点对账"])


@router.get("")
def get_reconcile(request: Request):
    store = request.app.state.store
    return service.payload(store, store.latest_run())


@router.post("/counts")
def post_counts(request: Request, payload: dict = Body(...)):
    store = request.app.state.store
    return service.submit(request.app.state.settings, store,
                          store.latest_run(), payload)
