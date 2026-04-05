from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    api_token: str
    app_host: str
    app_port: int
    lazycat_base_url: str
    lazycat_login_path: str
    lazycat_clientarea_path: str
    lazycat_email: str
    lazycat_password: str
    lazycat_target_hostname: str
    restart_cooldown_seconds: int
    stop_wait_seconds: int
    browser_timeout_ms: int
    playwright_headless: bool
    storage_state_path: Path
    artifact_dir: Path
    enter_panel_texts: tuple[str, ...]
    enter_panel_selectors: tuple[str, ...]
    stop_button_texts: tuple[str, ...]
    stop_button_selectors: tuple[str, ...]
    start_button_texts: tuple[str, ...]
    start_button_selectors: tuple[str, ...]
    confirm_button_texts: tuple[str, ...]
    confirm_button_selectors: tuple[str, ...]
    service_link_selectors: tuple[str, ...]
    service_link_skip_texts: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_token=os.getenv("API_TOKEN", "change-me"),
            app_host=os.getenv("APP_HOST", "0.0.0.0"),
            app_port=_env_int("APP_PORT", 8080),
            lazycat_base_url=os.getenv("LAZYCAT_BASE_URL", "https://lxc.lazycat.wiki").rstrip("/"),
            lazycat_login_path=os.getenv("LAZYCAT_LOGIN_PATH", "/login"),
            lazycat_clientarea_path=os.getenv("LAZYCAT_CLIENTAREA_PATH", "/clientarea"),
            lazycat_email=os.getenv("LAZYCAT_EMAIL", ""),
            lazycat_password=os.getenv("LAZYCAT_PASSWORD", ""),
            lazycat_target_hostname=os.getenv("LAZYCAT_TARGET_HOSTNAME", "").strip(),
            restart_cooldown_seconds=_env_int("RESTART_COOLDOWN_SECONDS", 300),
            stop_wait_seconds=_env_int("STOP_WAIT_SECONDS", 5),
            browser_timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 30000),
            playwright_headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
            storage_state_path=Path(os.getenv("STORAGE_STATE_PATH", "/data/storage-state.json")),
            artifact_dir=Path(os.getenv("ARTIFACT_DIR", "/data/artifacts")),
            enter_panel_texts=_env_list(
                "LAZYCAT_ENTER_PANEL_TEXTS",
                ("进入面板", "控制面板", "进入控制台", "Panel", "Manage Panel"),
            ),
            enter_panel_selectors=_env_list(
                "LAZYCAT_ENTER_PANEL_SELECTORS",
                (
                    "a[href*='container/dashboard']",
                    "a[href*='dashboard?hash=']",
                    "a[target='_blank'][href*=':8443/']",
                ),
            ),
            stop_button_texts=_env_list(
                "LAZYCAT_STOP_BUTTON_TEXTS",
                ("停止", "关机", "Shutdown", "Stop", "Power Off"),
            ),
            stop_button_selectors=_env_list("LAZYCAT_STOP_BUTTON_SELECTORS", tuple()),
            start_button_texts=_env_list(
                "LAZYCAT_START_BUTTON_TEXTS",
                ("启动", "开机", "Start", "Power On", "Boot"),
            ),
            start_button_selectors=_env_list("LAZYCAT_START_BUTTON_SELECTORS", tuple()),
            confirm_button_texts=_env_list(
                "LAZYCAT_CONFIRM_BUTTON_TEXTS",
                ("确认", "确定", "继续", "Yes", "Confirm", "Submit"),
            ),
            confirm_button_selectors=_env_list("LAZYCAT_CONFIRM_BUTTON_SELECTORS", tuple()),
            service_link_selectors=_env_list("LAZYCAT_SERVICE_LINK_SELECTORS", tuple()),
            service_link_skip_texts=_env_list(
                "LAZYCAT_SERVICE_LINK_SKIP_TEXTS",
                (
                    "首页",
                    "控制台",
                    "资源列表",
                    "账单",
                    "工单",
                    "支持",
                    "用户中心",
                    "退出",
                    "登录",
                    "注册",
                    "忘记密码",
                    "进入面板",
                    "停止",
                    "启动",
                ),
            ),
        )
