"""导出入口：流水、层、成本 CSV 与运行清单 JSON。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import service

router = APIRouter(prefix="/api/export", tags=["导出"])

KINDS = ("movements.csv", "layers.csv", "cost.csv", "manifest.json")


@router.get("/{kind}")
def export_file(request: Request, kind: str):
    store = request.app.state.store
    filename, text, media = service.build(
        store, store.latest_run(), request.app.state.settings, kind)
    return Response(
        content=text,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("")
def list_exports(request: Request):
    store = request.app.state.store
    return service.summary(store, store.latest_run())
