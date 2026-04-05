from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class StatusReport(BaseModel):
    status: str | None = None
    power_state: str | None = None
    is_online: bool | None = None
    instance_name: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RestartRequest(BaseModel):
    instance_name: str | None = None
    reason: str = "manual"
    force: bool = False


class RestartJob(BaseModel):
    id: str
    status: Literal["queued", "running", "success", "error", "skipped"]
    reason: str
    instance_name: str | None = None
    source: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
