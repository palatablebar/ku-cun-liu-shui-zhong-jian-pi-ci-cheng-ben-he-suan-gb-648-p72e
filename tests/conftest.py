"""测试装置：固定时区、样例目录、内存外置的临时 SQLite 与 TestClient。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TZ", "Asia/Shanghai")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.main import create_app  # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def settings(tmp_path):
    return config.Settings(
        db_path=str(tmp_path / "stockflow.sqlite"),
        sample_dir=str(SAMPLE_DIR),
    )


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def demo(client):
    response = client.post("/api/demo/run")
    assert response.status_code == 200, response.text
    return response.json()


def read_sample(name: str) -> bytes:
    return (SAMPLE_DIR / name).read_bytes()


def read_fixture(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def sample_text(name: str) -> str:
    return (SAMPLE_DIR / name).read_text(encoding="utf-8")


def parse_sample(settings, name: str = "movements_dirty.csv") -> dict:
    """样例 → 采信，供重放类测试直接复用。"""
    from app.modules.m1_ingest import service as m1_service
    from app.modules.m2_doc_rules import contract

    return m1_service.read_file(settings, name, read_sample(name),
                                contract.aliases("movements"))


def opening_rows(settings) -> list:
    from app.modules.m2_doc_rules import service as m2_service

    parsed = m2_service.read_opening(
        settings, "opening_balance.csv", read_sample("opening_balance.csv"))
    return parsed["rows"]


def build_rows(settings, parsed: dict) -> dict:
    from app.modules.m3_quality import service as m3_service

    return m3_service.build(settings, parsed)


def replay_sample(settings, shuffle: list[int] | None = None) -> dict:
    from app.modules.m4_replay import service as m4_service

    parsed = parse_sample(settings)
    if shuffle is not None:
        parsed["records"] = [parsed["records"][index] for index in shuffle]
    built = build_rows(settings, parsed)
    return m4_service.replay_rows(settings, built["rows"], opening_rows(settings))
