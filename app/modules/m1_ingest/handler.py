"""采信入口：导入出库流水或期初余额。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile

from ...errors import DomainError

router = APIRouter(prefix="/api/movements", tags=["采信"])


@router.post("/import")
async def import_movements(
    request: Request,
    sample: str = Form("movements_dirty.csv"),
    file: UploadFile | None = File(None),
):
    from ... import pipeline
    from .service import resolve_sample

    settings = request.app.state.settings
    store = request.app.state.store
    if file is not None and file.filename:
        raw = await file.read()
        filename = file.filename
    elif sample:
        filename, raw = resolve_sample(settings, sample)
    else:
        raise DomainError("E_INPUT", "需要上传文件或指定样例", 400)
    return pipeline.import_movements(settings, store, filename, raw)


@router.post("/opening")
async def import_opening(
    request: Request,
    sample: str = Form("opening_balance.csv"),
    file: UploadFile | None = File(None),
):
    from ... import pipeline
    from .service import resolve_sample

    settings = request.app.state.settings
    store = request.app.state.store
    if file is not None and file.filename:
        raw = await file.read()
        filename = file.filename
    elif sample:
        filename, raw = resolve_sample(settings, sample)
    else:
        raise DomainError("E_INPUT", "需要上传文件或指定样例", 400)
    return pipeline.import_opening(settings, store, filename, raw)


@router.get("")
def list_movements(request: Request, status: str = "", reason: str = ""):
    store = request.app.state.store
    run = store.latest_run()
    if not run:
        return {"run_id": None, "rows": [], "count": 0}
    rows = store.movements(run["run_id"], status=status or None, reason=reason or None)
    return {"run_id": run["run_id"], "count": len(rows), "rows": rows}
