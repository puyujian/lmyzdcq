from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from app.config import Settings
from app.models import RestartRequest
from app.models import StatusReport
from app.panel import LazyCatPanelClient
from app.restart_manager import RestartManager

LOGGER = logging.getLogger(__name__)

DOWN_STATES = {
    "down",
    "dead",
    "off",
    "offline",
    "poweroff",
    "poweredoff",
    "powered_off",
    "shutdown",
    "shut_down",
    "stopped",
}


def create_app(
    settings: Settings | None = None,
    manager: RestartManager | None = None,
) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    resolved_settings = settings or Settings.from_env()
    resolved_manager = manager or RestartManager(
        resolved_settings,
        LazyCatPanelClient(resolved_settings),
    )

    app = FastAPI(
        title="LazyCat Auto Rebooter",
        version="1.0.0",
        description="接收 VPS 状态上报，并在懒猫云面板里自动执行停止后再启动。",
    )
    app.state.settings = resolved_settings
    app.state.restart_manager = resolved_manager

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "summary": request.app.state.restart_manager.get_summary(),
            "base_url": request.app.state.settings.lazycat_base_url,
        }

    @app.post("/api/v1/vps/status")
    async def receive_status(
        report: StatusReport,
        request: Request,
        _: None = Depends(require_api_token),
    ) -> dict[str, object]:
        if not report_indicates_shutdown(report):
            return {
                "accepted": False,
                "message": "当前状态未判定为关机，不触发重启。",
                "normalized_state": normalize_report_state(report),
            }

        job, created = await request.app.state.restart_manager.enqueue_restart(
            reason="status-report",
            source=report.source,
            instance_name=report.instance_name,
            force=False,
        )
        return {
            "accepted": True,
            "restart_requested": created,
            "normalized_state": normalize_report_state(report),
            "job": job.model_dump(mode="json"),
        }

    @app.post("/api/v1/vps/restart")
    async def manual_restart(
        payload: RestartRequest,
        request: Request,
        _: None = Depends(require_api_token),
    ) -> dict[str, object]:
        job, created = await request.app.state.restart_manager.enqueue_restart(
            reason=payload.reason,
            source="manual",
            instance_name=payload.instance_name,
            force=payload.force,
        )
        return {
            "accepted": True,
            "restart_requested": created,
            "job": job.model_dump(mode="json"),
        }

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(
        job_id: str,
        request: Request,
        _: None = Depends(require_api_token),
    ) -> dict[str, object]:
        job = request.app.state.restart_manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job 不存在")
        return {"job": job.model_dump(mode="json")}

    return app


async def require_api_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header()] = None,
) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_token
    if not expected:
        return

    actual = (
        x_api_token
        or request.headers.get("api_key")
        or request.headers.get("api-key")
    )
    if not actual and authorization and authorization.lower().startswith("bearer "):
        actual = authorization[7:].strip()

    if actual != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API token 无效")


def report_indicates_shutdown(report: StatusReport) -> bool:
    if report.is_online is False:
        return True
    normalized = normalize_report_state(report)
    return normalized in DOWN_STATES


def normalize_report_state(report: StatusReport) -> str | None:
    for value in (report.power_state, report.status):
        if not value:
            continue
        cleaned = value.strip().replace("-", "_").replace(" ", "_").casefold()
        if cleaned:
            return cleaned
    return None


app = create_app()
