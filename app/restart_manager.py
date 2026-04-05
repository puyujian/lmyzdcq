from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models import RestartJob

LOGGER = logging.getLogger(__name__)


class RestartManager:
    def __init__(self, settings: Settings, panel_client) -> None:
        self._settings = settings
        self._panel_client = panel_client
        self._lock = asyncio.Lock()
        self._jobs: dict[str, RestartJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._active_job_id: str | None = None
        self._last_success_at: datetime | None = None

    async def enqueue_restart(
        self,
        *,
        reason: str,
        source: str | None,
        instance_name: str | None,
        force: bool = False,
    ) -> tuple[RestartJob, bool]:
        async with self._lock:
            active_job = self._get_active_job()
            if active_job is not None:
                return active_job, False

            if not force and self._last_success_at is not None:
                cooldown_until = self._last_success_at + timedelta(
                    seconds=self._settings.restart_cooldown_seconds,
                )
                if datetime.now(timezone.utc) < cooldown_until:
                    job = RestartJob(
                        id=str(uuid.uuid4()),
                        status="skipped",
                        reason=reason,
                        source=source,
                        instance_name=instance_name or self._settings.lazycat_target_hostname or None,
                        created_at=datetime.now(timezone.utc),
                        finished_at=datetime.now(timezone.utc),
                        details={
                            "message": "冷却时间内已成功执行过重启，跳过重复请求。",
                            "cooldown_until": cooldown_until.isoformat(),
                        },
                    )
                    self._jobs[job.id] = job
                    self._trim_jobs()
                    return job, False

            job = RestartJob(
                id=str(uuid.uuid4()),
                status="queued",
                reason=reason,
                source=source,
                instance_name=instance_name or self._settings.lazycat_target_hostname or None,
                created_at=datetime.now(timezone.utc),
            )
            self._jobs[job.id] = job
            self._active_job_id = job.id
            task = asyncio.create_task(self._run_job(job.id))
            self._tasks[job.id] = task
            self._trim_jobs()
            return job, True

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        self._jobs[job_id] = job.model_copy(
            update={
                "status": "running",
                "started_at": datetime.now(timezone.utc),
            },
        )
        current_job = self._jobs[job_id]
        try:
            result = await self._panel_client.restart_instance(current_job.instance_name)
            self._jobs[job_id] = current_job.model_copy(
                update={
                    "status": "success",
                    "finished_at": datetime.now(timezone.utc),
                    "details": result,
                },
            )
            self._last_success_at = datetime.now(timezone.utc)
            LOGGER.info("懒猫云重启成功 job_id=%s instance=%s", job_id, current_job.instance_name)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("懒猫云重启失败 job_id=%s", job_id)
            self._jobs[job_id] = current_job.model_copy(
                update={
                    "status": "error",
                    "finished_at": datetime.now(timezone.utc),
                    "error": str(exc),
                },
            )
        finally:
            async with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                self._tasks.pop(job_id, None)

    def get_job(self, job_id: str) -> RestartJob | None:
        return self._jobs.get(job_id)

    def get_summary(self) -> dict[str, str | None]:
        active_job = self._get_active_job()
        return {
            "active_job_id": active_job.id if active_job else None,
            "active_job_status": active_job.status if active_job else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
        }

    async def wait_for_job(self, job_id: str) -> RestartJob:
        task = self._tasks.get(job_id)
        if task is not None:
            await task
        return self._jobs[job_id]

    def _get_active_job(self) -> RestartJob | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        if job is None or job.status not in {"queued", "running"}:
            return None
        return job

    def _trim_jobs(self, max_items: int = 100) -> None:
        if len(self._jobs) <= max_items:
            return
        ordered_ids = sorted(self._jobs, key=lambda item: self._jobs[item].created_at)
        for old_id in ordered_ids[:-max_items]:
            if old_id == self._active_job_id:
                continue
            self._jobs.pop(old_id, None)
