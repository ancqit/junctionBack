"""Closed-unit health: the process is alive vs Junction is ready to serve."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .database import MONGODB_URL

UNIT_NAME = "junction-backend"
HEALTH_TIMEOUT_MS = max(int(os.getenv("HEALTH_TIMEOUT_MS", "1500")), 250)
_PLACEHOLDER_MONGO = "mongodb://invalid:27017"

router = APIRouter(tags=["health"])

_probe_client: MongoClient | None = None


class HealthReport(BaseModel):
    status: Literal["ok", "unavailable"]
    unit: str = UNIT_NAME
    mode: Literal["live", "ready"]
    checks: dict[str, str]


def _probe() -> MongoClient:
    global _probe_client
    if _probe_client is None:
        _probe_client = MongoClient(
            MONGODB_URL,
            serverSelectionTimeoutMS=HEALTH_TIMEOUT_MS,
            connectTimeoutMS=HEALTH_TIMEOUT_MS,
            socketTimeoutMS=HEALTH_TIMEOUT_MS,
            maxPoolSize=1,
            minPoolSize=0,
            retryWrites=True,
        )
    return _probe_client


def _config_ok() -> bool:
    mongo = (MONGODB_URL or "").strip()
    if not mongo or mongo == _PLACEHOLDER_MONGO:
        return False
    return len((os.getenv("JWT_SECRET") or "").strip()) >= 32


def _mongo_ok() -> bool:
    try:
        _probe().admin.command("ping")
        return True
    except PyMongoError:
        return False


def live_report() -> HealthReport:
    return HealthReport(status="ok", mode="live", checks={"process": "ok"})


def ready_report() -> HealthReport:
    config = "ok" if _config_ok() else "missing"
    if config != "ok":
        return HealthReport(
            status="unavailable",
            mode="ready",
            checks={"process": "ok", "config": config, "mongo": "skipped"},
        )
    mongo = "ok" if _mongo_ok() else "unreachable"
    return HealthReport(
        status="ok" if mongo == "ok" else "unavailable",
        mode="ready",
        checks={"process": "ok", "config": config, "mongo": mongo},
    )


def _respond(report: HealthReport) -> Response:
    code = status.HTTP_200_OK if report.status == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(
        content=report.model_dump_json(),
        media_type="application/json",
        status_code=code,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/live")
@router.get("/health/live")
def live() -> Response:
    """Process is up. No Mongo, no vendors. Use when you only need a heartbeat."""
    return _respond(live_report())


@router.get("/ready")
@router.get("/health")
def ready() -> Response:
    """
    Junction unit is ready to serve: required config plus a short Mongo ping.
    Set this as the Render Health Check Path. Caps wait at HEALTH_TIMEOUT_MS
    so a dead Atlas does not hang the probe or the site.
    """
    return _respond(ready_report())
