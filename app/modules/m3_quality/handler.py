"""隔离区入口：按原因码看隔离行，并导出 CSV。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import service

router = APIRouter(prefix="/api/quarantine", tags=["质量隔离"])


@router.get("")
def list_quarantine(request: Request, reason: str = ""):
    store = request.app.state.store
    run = store.latest_run()
    payload = service.payload(store, run)
    if reason:
        payload["rows"] = [row for row in payload["rows"] if row.get("reason") == reason]
        payload["count"] = len(payload["rows"])
        payload["distribution"] = [
            item for item in payload["distribution"] if item["code"] == reason
        ]
    return payload


@router.get(".csv")
def download_quarantine(request: Request):
    store = request.app.state.store
    filename, text = service.csv(store, store.latest_run())
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
