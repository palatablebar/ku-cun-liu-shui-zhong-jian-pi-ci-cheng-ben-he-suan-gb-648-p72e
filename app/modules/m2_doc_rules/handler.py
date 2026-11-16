"""单据规则入口：列定义、方向、单价义务与单位换算。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import service

router = APIRouter(prefix="/api/contract", tags=["单据规则"])


@router.get("")
def get_contract(request: Request):
    return service.payload(request.app.state.settings)
