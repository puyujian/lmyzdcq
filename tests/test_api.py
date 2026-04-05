from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import RestartJob
from app.panel import LazyCatPanelClient


class DummyManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._job = RestartJob(
            id="job-1",
            status="queued",
            reason="status-report",
            instance_name="demo-vps",
            source="uptime-kuma",
            created_at=datetime.now(timezone.utc),
        )

    async def enqueue_restart(self, *, reason, source, instance_name, force=False):
        self.calls.append(
            {
                "reason": reason,
                "source": source,
                "instance_name": instance_name,
                "force": force,
            },
        )
        return self._job, True

    def get_job(self, job_id: str):
        if job_id == self._job.id:
            return self._job
        return None

    def get_summary(self):
        return {"active_job_id": None, "active_job_status": None, "last_success_at": None}


def build_client() -> tuple[TestClient, DummyManager]:
    settings = Settings(
        api_token="secret-token",
        app_host="0.0.0.0",
        app_port=8080,
        lazycat_base_url="https://lxc.lazycat.wiki",
        lazycat_login_path="/login",
        lazycat_clientarea_path="/clientarea",
        lazycat_email="user@example.com",
        lazycat_password="password",
        lazycat_target_hostname="",
        restart_cooldown_seconds=300,
        stop_wait_seconds=5,
        browser_timeout_ms=30000,
        playwright_headless=True,
        storage_state_path=Path("data/test-storage-state.json"),
        artifact_dir=Path("data/test-artifacts"),
        enter_panel_texts=("进入面板",),
        enter_panel_selectors=tuple(),
        stop_button_texts=("停止",),
        stop_button_selectors=tuple(),
        start_button_texts=("启动",),
        start_button_selectors=tuple(),
        confirm_button_texts=("确认",),
        confirm_button_selectors=tuple(),
        service_link_selectors=tuple(),
        service_link_skip_texts=("首页",),
    )
    manager = DummyManager()
    app = create_app(settings=settings, manager=manager)
    client = TestClient(app)
    return client, manager


def test_healthz():
    client, _ = build_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_report_triggers_restart_when_offline():
    client, manager = build_client()
    response = client.post(
        "/api/v1/vps/status",
        headers={"X-Api-Token": "secret-token"},
        json={"status": "offline", "instance_name": "demo-vps", "source": "uptime-kuma"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["restart_requested"] is True
    assert manager.calls[0]["instance_name"] == "demo-vps"


def test_status_report_ignores_running_state():
    client, manager = build_client()
    response = client.post(
        "/api/v1/vps/status",
        headers={"X-Api-Token": "secret-token"},
        json={"status": "running", "instance_name": "demo-vps"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert manager.calls == []


def test_manual_restart_requires_token():
    client, _ = build_client()
    response = client.post("/api/v1/vps/restart", json={"reason": "manual"})
    assert response.status_code == 401


def test_get_job():
    client, _ = build_client()
    response = client.get("/api/v1/jobs/job-1", headers={"X-Api-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["job"]["id"] == "job-1"


def test_status_report_accepts_api_key_header():
    client, manager = build_client()
    response = client.post(
        "/api/v1/vps/status",
        headers={"api_key": "secret-token"},
        json={"status": "offline", "instance_name": "demo-vps", "source": "webhook"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert manager.calls[0]["source"] == "webhook"


def test_panel_context_options_ignore_https_errors():
    settings = Settings(
        api_token="secret-token",
        app_host="0.0.0.0",
        app_port=8080,
        lazycat_base_url="https://lxc.lazycat.wiki",
        lazycat_login_path="/login",
        lazycat_clientarea_path="/clientarea",
        lazycat_email="user@example.com",
        lazycat_password="password",
        lazycat_target_hostname="",
        restart_cooldown_seconds=300,
        stop_wait_seconds=5,
        browser_timeout_ms=30000,
        playwright_headless=True,
        storage_state_path=Path("data/test-storage-state.json"),
        artifact_dir=Path("data/test-artifacts"),
        enter_panel_texts=("进入面板",),
        enter_panel_selectors=tuple(),
        stop_button_texts=("停止", "关机"),
        stop_button_selectors=tuple(),
        start_button_texts=("启动", "开机"),
        start_button_selectors=tuple(),
        confirm_button_texts=("确认", "确定"),
        confirm_button_selectors=tuple(),
        service_link_selectors=tuple(),
        service_link_skip_texts=("首页",),
    )
    client = LazyCatPanelClient(settings)
    options = client._build_context_options()
    assert options["ignore_https_errors"] is True


def test_service_detail_href_detection():
    settings = Settings(
        api_token="secret-token",
        app_host="0.0.0.0",
        app_port=8080,
        lazycat_base_url="https://lxc.lazycat.wiki",
        lazycat_login_path="/login",
        lazycat_clientarea_path="/clientarea",
        lazycat_email="user@example.com",
        lazycat_password="password",
        lazycat_target_hostname="",
        restart_cooldown_seconds=300,
        stop_wait_seconds=5,
        browser_timeout_ms=30000,
        playwright_headless=True,
        storage_state_path=Path("data/test-storage-state.json"),
        artifact_dir=Path("data/test-artifacts"),
        enter_panel_texts=("进入面板",),
        enter_panel_selectors=tuple(),
        stop_button_texts=("停止", "关机"),
        stop_button_selectors=tuple(),
        start_button_texts=("启动", "开机"),
        start_button_selectors=tuple(),
        confirm_button_texts=("确认", "确定"),
        confirm_button_selectors=tuple(),
        service_link_selectors=tuple(),
        service_link_skip_texts=("首页",),
    )
    client = LazyCatPanelClient(settings)

    assert client._is_service_detail_href("servicedetail?id=6061") is True
    assert client._is_service_detail_href("/servicedetail?id=6061") is True
    assert (
        client._is_service_detail_href(
            "https://lxc.lazycat.wiki/clientarea?action=productdetails&id=6061",
        )
        is True
    )
    assert client._is_service_detail_href("/clientarea") is False
    assert client._is_service_detail_href("https://example.com/servicedetail?id=6061") is False
